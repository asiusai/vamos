#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
BUILD_DIR="$DIR/build"
ESP_IMG="$BUILD_DIR/esp.img"
KERNEL_IMAGE="$BUILD_DIR/Image"
DTB_FILE="$BUILD_DIR/qcs6490-radxa-dragon-q6a.dtb"
NCM_DTB_FILE="$BUILD_DIR/qcs6490-radxa-dragon-q6a-ncm.dtb"
QUP_FW="$DIR/kernel/firmware/qcom/qcm6490/qupv3fw.elf"
BOOTLOADER="$BUILD_DIR/BOOTAA64.EFI"
EDL_APPLICATION="$BUILD_DIR/EDLAA64.EFI"
. "$DIR/userspace/dsp_firmware.sh"

for input in "$KERNEL_IMAGE" "$DTB_FILE" "$QUP_FW"; do
  if [ ! -f "$input" ]; then
    echo "ERROR: missing $input"
    echo "Run: ./vamos build kernel"
    exit 1
  fi
done

echo "== Building vamOS ESP =="
"$DIR/tools/build/build_bootloader.sh"

# U-Boot must start the DSPs before Linux. Keep their firmware on FAT so
# root filesystem compression does not require a matching U-Boot decoder.
DSP_PACKAGE="$BUILD_DIR/downloads/${DSP_FIRMWARE_URL##*/}"
mkdir -p "$BUILD_DIR/downloads"
if ! printf '%s  %s\n' "$DSP_FIRMWARE_SHA256" "$DSP_PACKAGE" | sha256sum --check --status 2>/dev/null; then
  curl --fail --location --retry 3 --output "$DSP_PACKAGE.tmp" "$DSP_FIRMWARE_URL"
  printf '%s  %s\n' "$DSP_FIRMWARE_SHA256" "$DSP_PACKAGE.tmp" | sha256sum --check --status
  mv "$DSP_PACKAGE.tmp" "$DSP_PACKAGE"
fi
DSP_STAGE="$(mktemp -d)"
trap 'rm -rf "$DSP_STAGE"' EXIT
DSP_PATH="lib/firmware/qcom/qcs6490/radxa/dragon-q6a"
ar p "$DSP_PACKAGE" data.tar.xz | tar -xJf - -C "$DSP_STAGE" "./$DSP_PATH/adsp.mbn" "./$DSP_PATH/cdsp.mbn"

rm -f "$ESP_IMG"
truncate -s $((256 * 1024 * 1024)) "$ESP_IMG"
# Match UFS's 4 KiB logical sectors. At 256 MiB this requires FAT16;
# FAT32 would have fewer than its required 65,525 data clusters.
mkfs.vfat -F 16 -S 4096 -n VAMOS-NEW "$ESP_IMG" >/dev/null
mmd -i "$ESP_IMG" ::/EFI
mmd -i "$ESP_IMG" ::/EFI/BOOT
mmd -i "$ESP_IMG" ::/EFI/vamos
mcopy -i "$ESP_IMG" "$BOOTLOADER" ::/EFI/BOOT/BOOTAA64.EFI
mcopy -i "$ESP_IMG" "$KERNEL_IMAGE" ::/EFI/vamos/Image
mcopy -i "$ESP_IMG" "$EDL_APPLICATION" ::/EFI/vamos/edl.efi
mcopy -i "$ESP_IMG" "$QUP_FW" ::/EFI/vamos/qupv3fw.elf
mcopy -i "$ESP_IMG" "$DSP_STAGE/$DSP_PATH/adsp.mbn" "$DSP_STAGE/$DSP_PATH/cdsp.mbn" ::/EFI/vamos/
mcopy -i "$ESP_IMG" "$DTB_FILE" ::/qcs6490-radxa-dragon-q6a.dtb
cp "$DTB_FILE" "$NCM_DTB_FILE"
fdtput -t s "$NCM_DTB_FILE" /soc@0/usb@a600000 dr_mode peripheral
mcopy -i "$ESP_IMG" "$NCM_DTB_FILE" ::/qcs6490-radxa-dragon-q6a-ncm.dtb
mcopy -i "$ESP_IMG" "$BOOTLOADER" ::/Image
grub-editenv "$BUILD_DIR/grubenv" create
grub-editenv "$BUILD_DIR/grubenv" set generation=0 active=a pending= phase=stable root_a=PARTLABEL=rootfs_a root_b=PARTLABEL=rootfs_b edl_request=0 normal_request=0 usb_mode=ncm
mcopy -i "$ESP_IMG" "$BUILD_DIR/grubenv" ::/EFI/vamos/grubenv
mdir -i "$ESP_IMG" -/ ::/
