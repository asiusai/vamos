#!/bin/bash
set -e

#
# Kernel dev build - edit source, build, push, repeat.
# Does NOT reset the kernel tree. Works directly on kernel/linux/.
#
# Prerequisites: run build_kernel.sh once first (builds docker image + .config)
#
# Usage:
#   ./tools/build/dev.sh                  # build camss module
#   ./tools/build/dev.sh kernel           # full kernel + modules
#   ./tools/build/dev.sh boot             # build signed boot.img from kernel output
#   ./tools/build/dev.sh push [host]      # scp modules to device
#   ./tools/build/dev.sh flash [host]     # scp boot.img + flash to boot_b + reboot
#   ./tools/build/dev.sh config           # reconfigure kernel (after config changes)
#

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
KERNEL_DIR="$DIR/kernel/linux"
KBUILD_OUT="$DIR/build/kernel-out"
CONFIG_FRAGMENT="$DIR/kernel/configs/vamos.config"
DEVICE="${DEVICE:-comma@comma-9539449f}"
MODULE_KO="$KBUILD_OUT/drivers/media/platform/qcom/camss/qcom-camss.ko"

# Cross-compilation setup: match build_kernel.sh logic
# On aarch64/arm64 hosts the container's native gcc is the right compiler;
# on x86_64 hosts the container has aarch64-none-elf-gcc installed separately.
ARCH_HOST=$(uname -m)
if [ "$ARCH_HOST" != "aarch64" ] && [ "$ARCH_HOST" != "arm64" ]; then
  CROSS_COMPILE="aarch64-none-elf-"
  CC_CMD="ccache ${CROSS_COMPILE}gcc"
else
  CROSS_COMPILE=""
  CC_CMD="ccache gcc"
fi

MAKE="make ARCH=arm64 \
  ${CROSS_COMPILE:+CROSS_COMPILE=$CROSS_COMPILE} \
  CC='$CC_CMD' \
  CCACHE_DIR=$DIR/.ccache \
  KBUILD_BUILD_USER=vamos KBUILD_BUILD_HOST=vamos KCFLAGS=-w \
  O=$KBUILD_OUT"

# Reuse running container or start a new one
CONTAINER_NAME="vamos-dev"
if ! docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  echo "Starting dev container (persistent)..."
  docker run -d --name "$CONTAINER_NAME" \
    -u "$(id -u):$(id -g)" \
    -v "$DIR":"$DIR" -w "$DIR" \
    vamos-builder sleep infinity
fi

run() {
  docker exec -i -u "$(id -u):$(id -g)" "$CONTAINER_NAME" \
    bash -c "cd $KERNEL_DIR && $*"
}

case "${1:-module}" in
  module|camss)
    echo "Building camera modules..."
    run "$MAKE -j\$(nproc) modules"
    ls -lh "$MODULE_KO"
    find "$KBUILD_OUT/drivers/media/i2c" -name "ox03c10.ko" -o -name "os04c10.ko" 2>/dev/null | xargs ls -lh 2>/dev/null
    ;;

  kernel)
    echo "Building kernel + modules..."
    # Install DTS files
    cp "$DIR/kernel/dts/sdm845-comma-common.dtsi" "$KERNEL_DIR/arch/arm64/boot/dts/qcom/"
    cp "$DIR"/kernel/dts/sdm845-comma-*.dts "$KERNEL_DIR/arch/arm64/boot/dts/qcom/"
    run "$MAKE -j\$(nproc) Image.gz modules qcom/sdm845-comma-tizi.dtb qcom/sdm845-comma-mici.dtb"
    ;;

  config)
    echo "Reconfiguring kernel..."
    run "$MAKE defconfig"
    run "KCONFIG_CONFIG=$KBUILD_OUT/.config bash scripts/kconfig/merge_config.sh -m $KBUILD_OUT/.config $CONFIG_FRAGMENT"
    run "echo 'CONFIG_EXTRA_FIRMWARE_DIR=\"$DIR/kernel/firmware\"' >> $KBUILD_OUT/.config"
    run "$MAKE olddefconfig"
    ;;

  boot)
    echo "Building signed boot.img..."
    TOOLS="$DIR/tools/bin"
    OUT_DIR="$DIR/build"
    BOOT_IMG="$OUT_DIR/boot.img"
    IMAGE_GZ="$KBUILD_OUT/arch/arm64/boot/Image.gz"
    TMP_IMG=$(mktemp -d)

    # Concatenate kernel + DTBs
    cp "$IMAGE_GZ" "$TMP_IMG/Image.gz-dtb"
    for dtb in "$KBUILD_OUT"/arch/arm64/boot/dts/qcom/sdm845-comma-*.dtb; do
      [ -f "$dtb" ] && cat "$dtb" >> "$TMP_IMG/Image.gz-dtb"
    done

    # Create unsigned boot.img
    "$TOOLS/mkbootimg" \
      --kernel "$TMP_IMG/Image.gz-dtb" \
      --ramdisk /dev/null \
      --cmdline "console=ttyMSM0,115200n8 earlycon=msm_geni_serial,0xA84000 androidboot.hardware=qcom androidboot.console=ttyMSM0 ehci-hcd.park=3 lpm_levels.sleep_disabled=1 service_locator.enable=1 androidboot.selinux=permissive firmware_class.path=/lib/firmware/updates net.ifnames=0 fbcon=rotate:3" \
      --pagesize 4096 \
      --base 0x80000000 \
      --kernel_offset 0x8000 \
      --ramdisk_offset 0x8000 \
      --tags_offset 0x100 \
      --output "$TMP_IMG/boot.img.nonsecure"

    # Sign
    openssl dgst -sha256 -binary "$TMP_IMG/boot.img.nonsecure" > "$TMP_IMG/boot.sha256"
    openssl pkeyutl -sign -in "$TMP_IMG/boot.sha256" \
      -inkey "$DIR/tools/build/vble-qti.key" \
      -out "$TMP_IMG/boot.sig" \
      -pkeyopt digest:sha256 -pkeyopt rsa_padding_mode:pkcs1
    dd if=/dev/zero of="$TMP_IMG/boot.sig.padded" bs=2048 count=1 2>/dev/null
    dd if="$TMP_IMG/boot.sig" of="$TMP_IMG/boot.sig.padded" conv=notrunc 2>/dev/null
    cat "$TMP_IMG/boot.img.nonsecure" "$TMP_IMG/boot.sig.padded" > "$BOOT_IMG"

    rm -rf "$TMP_IMG"
    ls -lh "$BOOT_IMG"
    echo "Signed boot.img ready."
    ;;

  push)
    HOST="${2:-$DEVICE}"
    echo "Pushing modules to $HOST..."
    MODULES=()
    for ko in \
      drivers/media/mc/mc.ko \
      drivers/media/v4l2-core/videodev.ko \
      drivers/media/v4l2-core/v4l2-async.ko \
      drivers/media/v4l2-core/v4l2-fwnode.ko \
      drivers/media/v4l2-core/v4l2-cci.ko \
      drivers/media/common/videobuf2/videobuf2-common.ko \
      drivers/media/common/videobuf2/videobuf2-v4l2.ko \
      drivers/media/common/videobuf2/videobuf2-memops.ko \
      drivers/media/common/videobuf2/videobuf2-dma-sg.ko \
      drivers/media/platform/qcom/camss/qcom-camss.ko \
      drivers/media/i2c/ox03c10.ko \
      drivers/media/i2c/os04c10.ko \
    ; do
      [ -f "$KBUILD_OUT/$ko" ] && MODULES+=("$KBUILD_OUT/$ko")
    done
    scp "${MODULES[@]}" "$HOST:/tmp/"
    echo "Load: ssh $HOST 'sudo /tmp/load_camera.sh'"
    # Also push a load script
    cat <<'LOADEOF' | ssh "$HOST" "cat > /tmp/load_camera.sh && chmod +x /tmp/load_camera.sh"
#!/bin/sh
set -e
for m in mc videodev v4l2-async v4l2-fwnode v4l2-cci \
         videobuf2-common videobuf2-v4l2 videobuf2-memops videobuf2-dma-sg \
         qcom-camss ox03c10; do
  mod="/tmp/${m}.ko"
  [ -f "$mod" ] && insmod "$mod" 2>/dev/null && echo "loaded $m" || echo "skip $m (already loaded or missing)"
done
LOADEOF
    ;;

  flash)
    HOST="${2:-$DEVICE}"
    BOOT_IMG="$DIR/build/boot.img"
    if [ ! -f "$BOOT_IMG" ]; then
      echo "No boot.img found. Run: $0 boot"
      exit 1
    fi
    echo "Flashing boot.img to boot_b on $HOST..."
    scp "$BOOT_IMG" "$HOST:/tmp/boot.img"
    ssh "$HOST" "sudo dd if=/tmp/boot.img of=/dev/disk/by-partlabel/boot_b bs=4096 && sync && echo 'FLASH OK'"
    echo "Rebooting..."
    ssh "$HOST" "sudo reboot" || true
    ;;

  shell)
    docker exec -it -u "$(id -u):$(id -g)" "$CONTAINER_NAME" bash
    ;;

  clean)
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
    echo "Dev container removed."
    ;;

  *)
    echo "Usage: $0 {module|kernel|boot|push [host]|flash [host]|config|shell|clean}"
    ;;
esac
