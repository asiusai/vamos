#!/usr/bin/env bash
# Flash the full vamOS disk image to Dragon Q6A via EDL.
# Dragon must already be in EDL mode (05c6:9008 on USB).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
cd "$DIR"

DISK_IMG="$DIR/build/dragon.img"
LOADER="$DIR/firmware-dragon/flat_build/spinor/dragon-q6a/prog_firehose_ddr.elf"

if [ ! -f "$DISK_IMG" ]; then
  echo "ERROR: disk image not found at $DISK_IMG"
  echo "Run: ./vamos build disk"
  exit 1
fi
if [ ! -f "$LOADER" ]; then
  echo "ERROR: Firehose loader not found at $LOADER"
  echo "Re-download firmware-dragon/ from dl.radxa.com (see tools/flash/README)."
  exit 1
fi

if ! lsusb -d 05c6:9008 >/dev/null 2>&1; then
  echo "WARN: Dragon is not in EDL mode (05c6:9008 not on USB)."
  echo "Enter EDL via BIOS menu 'Reboot into EDL / 9008' or the EDL button, then retry."
  exit 1
fi

echo "== Flashing $DISK_IMG to Dragon NVMe =="
sudo edl-ng --memory=nvme write-sector 0 "$DISK_IMG" --loader="$LOADER"

if [ "${VAMOS_NO_RESET:-}" != "1" ]; then
  echo "== Resetting device =="
  sudo edl-ng reset --loader="$LOADER"
fi
