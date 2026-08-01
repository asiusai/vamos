#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
BUILD_DIR="$DIR/build"
ESP_IMG="$BUILD_DIR/esp.img"
KERNEL_IMAGE="$BUILD_DIR/Image"
DTB_FILE="$BUILD_DIR/qcs6490-radxa-dragon-q6a.dtb"
BOOTLOADER="$BUILD_DIR/BOOTAA64.EFI"

for input in "$KERNEL_IMAGE" "$DTB_FILE"; do
  if [ ! -f "$input" ]; then
    echo "ERROR: missing $input"
    echo "Run: ./vamos build kernel"
    exit 1
  fi
done

echo "== Building vamOS ESP =="
"$DIR/tools/build/build_bootloader.sh"
rm -f "$ESP_IMG"
truncate -s $((256 * 1024 * 1024)) "$ESP_IMG"
mkfs.vfat -F 32 -n VAMOS-NEW "$ESP_IMG" >/dev/null
mmd -i "$ESP_IMG" ::/EFI
mmd -i "$ESP_IMG" ::/EFI/BOOT
mmd -i "$ESP_IMG" ::/EFI/vamos
mcopy -i "$ESP_IMG" "$BOOTLOADER" ::/EFI/BOOT/BOOTAA64.EFI
mcopy -i "$ESP_IMG" "$KERNEL_IMAGE" ::/EFI/vamos/Image
mcopy -i "$ESP_IMG" "$DTB_FILE" ::/qcs6490-radxa-dragon-q6a.dtb
mcopy -i "$ESP_IMG" "$BOOTLOADER" ::/Image
grub-editenv "$BUILD_DIR/grubenv" create
grub-editenv "$BUILD_DIR/grubenv" set generation=0 active=a pending= phase=stable root_a=PARTLABEL=rootfs_a root_b=PARTLABEL=rootfs_b
mcopy -i "$ESP_IMG" "$BUILD_DIR/grubenv" ::/EFI/vamos/grubenv
mdir -i "$ESP_IMG" -/ ::/
