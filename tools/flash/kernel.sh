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
"$DIR/tools/build/build_esp.sh"

echo "== Flashing ESP (kernel + dtb) to Dragon =="
EDL=(sudo edl-ng --memory=nvme --loader="$LOADER")
GPT_OUTPUT="$("${EDL[@]}" printgpt 2>&1)"
printf '%s\n' "$GPT_OUTPUT"

if grep -Eq 'Name:[[:space:]]+esp_a$' <<<"$GPT_OUTPUT" && grep -Eq 'Name:[[:space:]]+esp_b$' <<<"$GPT_OUTPUT"; then
  # This is the recovery/development path. Keep both ESPs bootable regardless
  # of the current EFI slot; use device-update for rollback-safe trials.
  ESP_PARTITIONS=(esp_a esp_b)
elif grep -Eq 'Name:[[:space:]]+esp$' <<<"$GPT_OUTPUT"; then
  ESP_PARTITIONS=(esp)
else
  echo "ERROR: no supported ESP partition layout found"
  exit 1
fi

for partition in "${ESP_PARTITIONS[@]}"; do
  echo "== Writing $partition =="
  "${EDL[@]}" write-part "$partition" "$ESP_IMG"
done

if [ "${VAMOS_NO_RESET:-}" != "1" ]; then
  echo "== Resetting device =="
  "${EDL[@]}" reset
fi
