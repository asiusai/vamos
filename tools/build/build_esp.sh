#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
BUILD_DIR="$DIR/build"
ESP_IMG="$BUILD_DIR/esp.img"
KERNEL_IMAGE="$BUILD_DIR/Image"
DTB_FILE="$BUILD_DIR/qcs6490-radxa-dragon-q6a.dtb"

for input in "$KERNEL_IMAGE" "$DTB_FILE"; do
  if [ ! -f "$input" ]; then
    echo "ERROR: missing $input"
    echo "Run: ./vamos build kernel"
    exit 1
  fi
done

echo "== Building vamOS ESP =="
rm -f "$ESP_IMG"
truncate -s $((256 * 1024 * 1024)) "$ESP_IMG"
mkfs.vfat -F 32 -n VAMOS-ESP "$ESP_IMG" >/dev/null
mmd -i "$ESP_IMG" ::/EFI
mmd -i "$ESP_IMG" ::/EFI/BOOT
mcopy -i "$ESP_IMG" "$KERNEL_IMAGE" ::/EFI/BOOT/BOOTAA64.EFI
mcopy -i "$ESP_IMG" "$DTB_FILE" ::/qcs6490-radxa-dragon-q6a.dtb
mcopy -i "$ESP_IMG" "$KERNEL_IMAGE" ::/Image
mdir -i "$ESP_IMG" -/ ::/
