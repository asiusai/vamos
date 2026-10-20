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

storage="${VAMOS_EDL_MEMORY:-Ufs}"
if [ "${storage,,}" != ufs ]; then
  echo "ERROR: VAMOS_EDL_MEMORY must be Ufs" >&2
  exit 2
fi
DISK_IMG="$DIR/build/dragon-ufs.img"
USERDATA_IMG="$DIR/build/userdata-ufs.img"
OPENPILOT_IMG="$DIR/build/userdata-openpilot-ufs.img"
LAYOUT="$DIR/build/factory-layout-ufs.json"
MANIFEST_DIR="$DIR/build/flash-ufs"
RAWPROGRAM_DIR="$DIR/build/manual-flash-ufs"
MANIFEST="$MANIFEST_DIR/flash.json"
LOADER="$DIR/firmware-dragon/flat_build/spinor/dragon-q6a/prog_firehose_ddr.elf"

for input in "$DISK_IMG" "$USERDATA_IMG" "$OPENPILOT_IMG" "$LAYOUT" "$LOADER"; do
  if [ ! -f "$input" ]; then
    echo "ERROR: required factory artifact not found: $input" >&2
    echo "Run: ./vamos build disk --storage ${storage,,}" >&2
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
options=()
[ "$choice" = openpilot ] || options+=(--without-openpilot)
[ "${VAMOS_NO_RESET:-}" != "1" ] || options+=(--no-reset)
python3 -m tools.flash.flash_factory "$MANIFEST" "$RAWPROGRAM_DIR" \
  --loader "$LOADER" --memory Ufs "${options[@]}"
