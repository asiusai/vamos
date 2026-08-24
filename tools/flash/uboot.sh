#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
. "$DIR/tools/flash/edl_usb.sh"

LOADER="$DIR/firmware-dragon/flat_build/spinor/dragon-q6a/prog_firehose_ddr.elf"
CORE="$DIR/build/u-boot/u-boot-xbl-core.bin"
CORE_WITH_SL="$DIR/build/u-boot/u-boot-xbl-core-secure-launch.bin"
EMBED_SL="$DIR/tools/build/embed_secure_launch.py"
QTESTSIGN="$DIR/bootloader/qtestsign/qtestsign.py"
PATCHXBL="$DIR/bootloader/qtestsign/patchxbl.py"
STOCK="$DIR/build/xbl-stock-dragon-q6a.bin"
TZAPPS="$DIR/build/tzapps-stock-dragon-q6a.bin"
PLAT="$DIR/build/plat-stock-dragon-q6a.bin"
UNSIGNED="$DIR/build/u-boot-dragon-q6a-xbl.elf"
UBOOT="$DIR/build/u-boot-dragon-q6a-xbl.mbn"
VERIFY="$DIR/build/xbl-verify-dragon-q6a.bin"
XBL_SIZE=$((6 * 1024 * 1024))
restore=0

case "${1:-}" in
  "") ;;
  --restore-stock) restore=1 ;;
  *) echo "Usage: ./vamos flash uboot [--restore-stock]" >&2; exit 2 ;;
esac

if [ ! -f "$LOADER" ]; then
  echo "ERROR: missing Firehose loader: $LOADER" >&2
  exit 1
fi
if [ "$restore" -eq 0 ] && [ ! -f "$CORE" ]; then
  echo "ERROR: missing U-Boot core: $CORE" >&2
  echo "Run: ./vamos build uboot" >&2
  exit 1
fi

if ! lsusb -d 05c6:9008 >/dev/null 2>&1; then
  echo "== Requesting EDL from the running Dragon =="
  "$DIR/dragon.py" edl --software-only
fi
detach_qcserial
EDL=(sudo edl-ng --maxpayload=65536 --memory=Spinor --slot=0 --loader="$LOADER")

if [ ! -f "$STOCK" ]; then
  echo "== Saving current XBL firmware =="
  "${EDL[@]}" read-part XBL "$STOCK"
  if [ "$(stat -c%s "$STOCK")" -ne "$XBL_SIZE" ]; then
    echo "ERROR: stock XBL backup is not exactly 6 MiB" >&2
    exit 1
  fi
fi

if [ "$restore" -eq 1 ]; then
  payload="$STOCK"
else
  if ! command -v uefiextract >/dev/null 2>&1; then
    echo "ERROR: uefiextract is required to derive Secure Launch data from stock XBL" >&2
    echo "Install the uefitool-cli package" >&2
    exit 1
  fi

  extract_dir="$(mktemp -d)"
  trap 'rm -rf -- "$extract_dir"' EXIT
  cp "$STOCK" "$extract_dir/xbl.bin"
  (cd "$extract_dir" && uefiextract xbl.bin all >/dev/null)
  mapfile -d '' resources < <(find "$extract_dir/xbl.bin.dump" -type f \
    -path '*resource.bin/1 Raw section/body.bin' -print0)
  if [ "${#resources[@]}" -ne 1 ]; then
    echo "ERROR: expected exactly one Secure Launch resource.bin in stock XBL; found ${#resources[@]}" >&2
    exit 1
  fi
  echo "== Reading device Secure Launch dependencies =="
  "${EDL[@]}" read-part TZAPPS "$TZAPPS"
  "${EDL[@]}" read-part PLAT "$PLAT"
  python3 "$EMBED_SL" --core "$CORE" --resource "${resources[0]}" \
    --tzapps "$TZAPPS" --plat "$PLAT" \
    --output "$CORE_WITH_SL"

  echo "== Replacing the Dragon EDK2 XBL segment with U-Boot =="
  python3 "$PATCHXBL" -c "$CORE_WITH_SL" -o "$UNSIGNED" "$STOCK"
  python3 "$QTESTSIGN" -v6 sbl1 "$UNSIGNED" -o "$UBOOT"
  payload="$UBOOT"
fi
if [ "$(stat -c%s "$payload")" -gt "$XBL_SIZE" ]; then
  echo "ERROR: XBL payload is larger than 6 MiB: $payload" >&2
  exit 1
fi

echo "== Writing XBL: $(basename "$payload") =="
"${EDL[@]}" write-part XBL "$payload"
"${EDL[@]}" read-part XBL "$VERIFY"

payload_hash="$(head -c "$(stat -c%s "$payload")" "$VERIFY" | sha256sum | cut -d' ' -f1)"
expected_hash="$(sha256sum "$payload" | cut -d' ' -f1)"
if [ "$payload_hash" != "$expected_hash" ]; then
  echo "ERROR: XBL readback does not match payload" >&2
  exit 1
fi
echo "Verified XBL payload sha256 $expected_hash"

echo "== Resetting Dragon =="
"${EDL[@]}" reset
