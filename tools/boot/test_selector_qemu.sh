#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
BUILD_DIR="$DIR/build"
SELECTOR="$BUILD_DIR/BOOTAA64.EFI"
AAVMF_CODE=/usr/share/AAVMF/AAVMF_CODE.no-secboot.fd
AAVMF_VARS=/usr/share/AAVMF/AAVMF_VARS.fd

for command in grub-mkstandalone grub-editenv qemu-system-aarch64 sgdisk mcopy mmd mkfs.vfat; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "SKIP: $command is required for the ARM64 EFI integration test"
    exit 0
  fi
done
if [ ! -f "$AAVMF_CODE" ] || [ ! -f "$AAVMF_VARS" ]; then
  echo "SKIP: qemu-efi-aarch64 firmware is not installed"
  exit 0
fi

"$DIR/tools/build/build_bootloader.sh"
GRUB_MODULES="$BUILD_DIR/grub-arm64/root/usr/lib/grub/arm64-efi"

temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

PAYLOAD="$temporary/PAYLOAD.EFI"
grub-mkstandalone \
  --directory="$GRUB_MODULES" \
  --format=arm64-efi \
  --output="$PAYLOAD" \
  --compress=xz \
  --locales="" \
  --fonts="" \
  --install-modules="normal echo sleep halt" \
  "boot/grub/grub.cfg=$DIR/tools/boot/qemu-payload.cfg"

make_env() {
  output="$1"
  generation="$2"
  active="$3"
  pending="$4"
  phase="$5"
  grub-editenv "$output" create
  grub-editenv "$output" set \
    generation="$generation" active="$active" pending="$pending" phase="$phase" \
    root_a=PARTLABEL=rootfs_a root_b=PARTLABEL=rootfs_b
}

make_esp() {
  slot="$1"
  env="$2"
  output="$3"
  truncate -s 64M "$output"
  mkfs.vfat -F 32 -n "VAMOS-${slot^^}" "$output" >/dev/null
  mmd -i "$output" ::/EFI ::/EFI/BOOT ::/EFI/vamos
  mcopy -i "$output" "$SELECTOR" ::/Image
  mcopy -i "$output" "$SELECTOR" ::/EFI/BOOT/BOOTAA64.EFI
  mcopy -i "$output" "$PAYLOAD" ::/EFI/vamos/Image
  mcopy -i "$output" "$env" ::/EFI/vamos/grubenv
}

ENV_A="$temporary/grubenv-a"
ENV_B="$temporary/grubenv-b"
make_env "$ENV_A" 10 a b armed
make_env "$ENV_B" 10 a b armed
make_esp a "$ENV_A" "$temporary/esp-a.img"
make_esp b "$ENV_B" "$temporary/esp-b.img"

DISK="$temporary/disk.img"
truncate -s 144M "$DISK"
sgdisk --clear \
  --new=1:2048:+64M --typecode=1:ef00 --change-name=1:esp_a \
  --new=2:0:+64M --typecode=2:ef00 --change-name=2:esp_b \
  "$DISK" >/dev/null
START_A="$(sgdisk --info=1 "$DISK" | sed -n 's/^First sector: \([0-9]*\).*/\1/p')"
START_B="$(sgdisk --info=2 "$DISK" | sed -n 's/^First sector: \([0-9]*\).*/\1/p')"
dd if="$temporary/esp-a.img" of="$DISK" bs=512 seek="$START_A" conv=notrunc status=none
dd if="$temporary/esp-b.img" of="$DISK" bs=512 seek="$START_B" conv=notrunc status=none

run_boot() {
  output="$1"
  cp "$AAVMF_VARS" "$temporary/vars.fd"
  set +e
  timeout --signal=TERM 12 qemu-system-aarch64 \
    -machine virt,gic-version=3 \
    -cpu cortex-a57 \
    -m 512M \
    -nographic \
    -no-reboot \
    -drive if=pflash,format=raw,readonly=on,file="$AAVMF_CODE" \
    -drive if=pflash,format=raw,file="$temporary/vars.fd" \
    -drive if=none,id=nvme,format=raw,file="$DISK" \
    -device virtio-blk-device,drive=nvme,bootindex=0 \
    >"$output" 2>&1
  status=$?
  set -e
  if [ "$status" -ne 0 ] && [ "$status" -ne 124 ]; then
    cat "$output"
    return "$status"
  fi
}

expect_log() {
  pattern="$1"
  log_file="$2"
  if ! grep -q "$pattern" "$log_file"; then
    echo "ERROR: missing QEMU output: $pattern" >&2
    cat "$log_file" >&2
    exit 1
  fi
}

extract_env() {
  start="$1"
  output="$2"
  dd if="$DISK" of="$temporary/extracted.img" bs=512 skip="$start" count=$((64 * 1024 * 1024 / 512)) status=none
  mcopy -i "$temporary/extracted.img" ::/EFI/vamos/grubenv "$output"
}

echo "== QEMU: one-shot trial from a fresh board =="
run_boot "$temporary/trial.log"
expect_log "vamOS: booting slot b (trial)" "$temporary/trial.log"
extract_env "$START_A" "$temporary/after-trial-a"
extract_env "$START_B" "$temporary/after-trial-b"
grep -q '^phase=attempted$' < <(grub-editenv "$temporary/after-trial-a" list)
grep -q '^phase=attempted$' < <(grub-editenv "$temporary/after-trial-b" list)

echo "== QEMU: automatic rollback on another fresh board =="
run_boot "$temporary/rollback.log"
expect_log "vamOS: rolling back uncommitted slot b" "$temporary/rollback.log"
expect_log "vamOS: booting slot a" "$temporary/rollback.log"
extract_env "$START_A" "$temporary/after-rollback-a"
extract_env "$START_B" "$temporary/after-rollback-b"
grep -q '^phase=stable$' < <(grub-editenv "$temporary/after-rollback-a" list)
grep -q '^pending=$' < <(grub-editenv "$temporary/after-rollback-b" list)

echo "ARM64 EFI selector trial, rollback, redundant persistence, and board portability passed"
