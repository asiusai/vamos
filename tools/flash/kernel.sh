#!/usr/bin/env bash
# Flash just the ESP partition (kernel + dtb) to Dragon Q6A eMMC via EDL.
# Rebuilds the ESP image from build/Image + DTB each time.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
cd "$DIR"

ESP_IMG="$DIR/build/esp.img"
KERNEL_IMAGE="$DIR/build/Image"
DTB_FILE="$DIR/build/qcs6490-radxa-dragon-q6a.dtb"
LOADER="$DIR/firmware-dragon/flat_build/spinor/dragon-q6a/prog_firehose_ddr.elf"

if [ ! -f "$KERNEL_IMAGE" ]; then
  echo "ERROR: kernel Image not found at $KERNEL_IMAGE"
  echo "Run: ./vamos build kernel"
  exit 1
fi
if [ ! -f "$DTB_FILE" ]; then
  echo "ERROR: DTB not found at $DTB_FILE"
  echo "Run: ./vamos build kernel"
  exit 1
fi
if [ ! -f "$LOADER" ]; then
  echo "ERROR: Firehose loader not found at $LOADER"
  exit 1
fi

if ! lsusb -d 05c6:9008 >/dev/null 2>&1; then
  echo "WARN: Dragon is not in EDL mode (05c6:9008 not on USB)."
  echo "Enter EDL via BIOS menu 'Reboot into EDL / 9008' or the EDL button, then retry."
  exit 1
fi

echo "== Building ESP image =="
rm -f "$ESP_IMG"
truncate -s $(( 256 * 1024 * 1024 )) "$ESP_IMG"
mkfs.vfat -F 32 -n VAMOS-ESP "$ESP_IMG" >/dev/null
mmd -i "$ESP_IMG" ::/EFI
mmd -i "$ESP_IMG" ::/EFI/BOOT
mcopy -i "$ESP_IMG" "$KERNEL_IMAGE" ::/EFI/BOOT/BOOTAA64.EFI
mcopy -i "$ESP_IMG" "$DTB_FILE" ::/qcs6490-radxa-dragon-q6a.dtb
mcopy -i "$ESP_IMG" "$KERNEL_IMAGE" ::/Image

echo "== Flashing ESP (kernel + dtb) to Dragon =="
sudo edl-ng --memory=Sdcc --slot=0 write-part esp "$ESP_IMG" --loader="$LOADER"

if [ "${VAMOS_NO_RESET:-}" != "1" ]; then
  echo "== Resetting device =="
  sudo edl-ng reset --loader="$LOADER"
fi
