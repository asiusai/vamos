#!/usr/bin/env bash
# Flash just the ESP partition (kernel + dtb) to Dragon Q6A eMMC via EDL.
# Faster iteration than a full system flash once the GPT exists on-device.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
cd "$DIR"

ESP_IMG="$DIR/build/esp.img"
LOADER="$DIR/firmware-dragon/flat_build/spinor/dragon-q6a/prog_firehose_ddr.elf"

if [ ! -f "$ESP_IMG" ]; then
  echo "ERROR: ESP image not found at $ESP_IMG"
  echo "Run: ./vamos build disk"
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

echo "== Flashing ESP (kernel + dtb) to Dragon =="
sudo edl-ng --memory=Sdcc --slot=0 write-part esp "$ESP_IMG" --loader="$LOADER"

if [ "${VAMOS_NO_RESET:-}" != "1" ]; then
  echo "== Resetting device =="
  sudo edl-ng reset --loader="$LOADER"
fi
