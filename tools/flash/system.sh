#!/usr/bin/env bash
# Flash the common final-layout factory disk and optionally apply openpilot to
# userdata. Dragon must already be in EDL mode (05c6:9008 on USB).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
cd "$DIR"
. "$DIR/tools/flash/edl_usb.sh"

choice=openpilot
case "${1:-}" in
  ""|--with-openpilot) choice=openpilot ;;
  --without-openpilot) choice=base ;;
  *) echo "Usage: ./vamos flash system [--with-openpilot|--without-openpilot]" >&2; exit 2 ;;
esac

DISK_IMG="$DIR/build/dragon.img"
USERDATA_IMG="$DIR/build/userdata.img"
OPENPILOT_IMG="$DIR/build/userdata-openpilot.img"
LAYOUT="$DIR/build/factory-layout.json"
MANIFEST="$DIR/build/flash/flash.json"
LOADER="$DIR/firmware-dragon/flat_build/spinor/dragon-q6a/prog_firehose_ddr.elf"

for input in "$DISK_IMG" "$USERDATA_IMG" "$OPENPILOT_IMG" "$LAYOUT" "$LOADER"; do
  if [ ! -f "$input" ]; then
    echo "ERROR: required factory artifact not found: $input" >&2
    echo "Run: ./vamos build disk" >&2
    exit 1
  fi
done

if [ ! -f "$MANIFEST" ] || [ "$DISK_IMG" -nt "$MANIFEST" ] || \
   [ "$USERDATA_IMG" -nt "$MANIFEST" ] || [ "$OPENPILOT_IMG" -nt "$MANIFEST" ] || \
   [ "$LAYOUT" -nt "$MANIFEST" ]; then
  echo "== Packaging local factory operations =="
  python3 "$DIR/tools/build/package_flash.py"
fi

if ! lsusb -d 05c6:9008 >/dev/null 2>&1; then
  echo "WARN: Dragon is not in EDL mode (05c6:9008 not on USB)." >&2
  echo "Enter EDL via the recovery control, then retry." >&2
  exit 1
fi
detach_qcserial

EDL=(sudo edl-ng --memory=nvme --loader="$LOADER")
RAWPROGRAM_DIR="$DIR/build/manual-flash"

flash_payload() {
  local payload="$1"
  local output="$RAWPROGRAM_DIR/$payload"
  python3 "$DIR/tools/flash/prepare_rawprogram.py" "$MANIFEST" "$output" --payload "$payload"

  local query
  if [ "$payload" = base ]; then
    query='.base.operations[] | select(.operation == "erase") | [.offset, .size] | @tsv'
  else
    query='.optional_payloads.openpilot.operations[] | select(.operation == "erase") | [.offset, .size] | @tsv'
  fi
  while IFS=$'\t' read -r offset size; do
    [ -n "$offset" ] || continue
    echo "== Erasing $payload range at byte $offset ($size bytes) =="
    "${EDL[@]}" erase-sector "$((offset / 512))" "$((size / 512))"
  done < <(jq -r "$query" "$MANIFEST")

  if grep -q '<program ' "$output/rawprogram0.xml"; then
    echo "== Programming $payload ranges =="
    "${EDL[@]}" rawprogram "$output/rawprogram0.xml"
  fi
}

flash_payload base
if [ "$choice" = openpilot ]; then
  flash_payload openpilot
fi

if [ "${VAMOS_NO_RESET:-}" != "1" ]; then
  echo "== Resetting device =="
  "${EDL[@]}" reset
fi
