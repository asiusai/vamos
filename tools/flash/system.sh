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

storage="${VAMOS_EDL_MEMORY:-Nvme}"
case "${storage,,}" in
  nvme)
    DISK_IMG="$DIR/build/dragon.img"
    USERDATA_IMG="$DIR/build/userdata.img"
    OPENPILOT_IMG="$DIR/build/userdata-openpilot.img"
    LAYOUT="$DIR/build/factory-layout.json"
    MANIFEST_DIR="$DIR/build/flash"
    RAWPROGRAM_DIR="$DIR/build/manual-flash"
    ;;
  ufs)
    DISK_IMG="$DIR/build/dragon-ufs.img"
    USERDATA_IMG="$DIR/build/userdata-ufs.img"
    OPENPILOT_IMG="$DIR/build/userdata-openpilot-ufs.img"
    LAYOUT="$DIR/build/factory-layout-ufs.json"
    MANIFEST_DIR="$DIR/build/flash-ufs"
    RAWPROGRAM_DIR="$DIR/build/manual-flash-ufs"
    ;;
  *)
    echo "ERROR: VAMOS_EDL_MEMORY must be Ufs or Nvme" >&2
    exit 2
    ;;
esac
MANIFEST="$MANIFEST_DIR/flash.json"
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
  python3 "$DIR/tools/build/package_flash.py" \
    --image "$DISK_IMG" \
    --layout "$LAYOUT" \
    --userdata "$USERDATA_IMG" \
    --openpilot-userdata "$OPENPILOT_IMG" \
    --output-dir "$MANIFEST_DIR"
fi

if ! lsusb -d 05c6:9008 >/dev/null 2>&1; then
  echo "WARN: Dragon is not in EDL mode (05c6:9008 not on USB)." >&2
  echo "Enter EDL via the recovery control, then retry." >&2
  exit 1
fi
detach_qcserial

detect_edl_storage "$LOADER"
EDL=(sudo edl-ng "${EDL_TRANSPORT_ARGS[@]}" "${EDL_STORAGE_ARGS[@]}" --loader="$LOADER")
FLASH_SECTOR_SIZE="$(jq -r '.sector_size' "$MANIFEST")"
case "$FLASH_SECTOR_SIZE" in
  512|4096) ;;
  *) echo "ERROR: unsupported flash sector size: $FLASH_SECTOR_SIZE" >&2; exit 1 ;;
esac

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
    "${EDL[@]}" erase-sector \
      "$((offset / FLASH_SECTOR_SIZE))" "$((size / FLASH_SECTOR_SIZE))"
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
