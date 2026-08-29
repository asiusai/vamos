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
DEFAULT_STOCK="$DIR/build/xbl-stock-dragon-q6a.bin"
STOCK_OVERRIDE="${VAMOS_XBL_STOCK:-}"
STOCK_BACKUPS="$DIR/build/xbl-backups"
CURRENT="$DIR/build/xbl-current-dragon-q6a.bin"
TZAPPS="$DIR/build/tzapps-stock-dragon-q6a.bin"
PLAT="$DIR/build/plat-stock-dragon-q6a.bin"
UNSIGNED="$DIR/build/u-boot-dragon-q6a-xbl.elf"
SIGNED="$DIR/build/u-boot-dragon-q6a-xbl.signed.mbn"
UBOOT="$DIR/build/u-boot-dragon-q6a-xbl.mbn"
VERIFY="$DIR/build/xbl-verify-dragon-q6a.bin"
XBL_SIZE=$((6 * 1024 * 1024))
# Stock XBL revisions physically validated with this U-Boot payload. Keep the
# exact hash check: the stock image also supplies board-matched Secure Launch
# data and is the recovery image if an attended bring-up fails.
SUPPORTED_STOCK_SHA256=(
  62e35c5aa2b564ed0f604debee94d284c5842b9db46f35194948c41717a0ded5
  76a5964746253aaead8c77ed20987c430d05f80fe6e3dbe6c94c34474308c5cd
)
restore=0

stock_is_supported() {
  local candidate="$1"
  local supported

  for supported in "${SUPPORTED_STOCK_SHA256[@]}"; do
    if [ "$candidate" = "$supported" ]; then
      return 0
    fi
  done
  return 1
}

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

echo "== Reading connected Dragon XBL =="
"${EDL[@]}" read-part XBL "$CURRENT"
if [ "$(stat -c%s "$CURRENT")" -ne "$XBL_SIZE" ]; then
  echo "ERROR: connected XBL is not exactly 6 MiB" >&2
  exit 1
fi
current_hash="$(sha256sum "$CURRENT" | cut -d' ' -f1)"

if [ "$restore" -eq 1 ]; then
  STOCK="${STOCK_OVERRIDE:-$DEFAULT_STOCK}"
elif [ -n "$STOCK_OVERRIDE" ]; then
  STOCK="$STOCK_OVERRIDE"
elif stock_is_supported "$current_hash"; then
  mkdir -p "$STOCK_BACKUPS"
  STOCK="$STOCK_BACKUPS/xbl-stock-dragon-q6a-${current_hash:0:8}.bin"
  if [ -f "$STOCK" ] && ! cmp -s "$CURRENT" "$STOCK"; then
    echo "ERROR: stock XBL backup does not match its content hash: $STOCK" >&2
    exit 1
  fi
  if [ ! -f "$STOCK" ]; then
    cp "$CURRENT" "$STOCK.tmp"
    mv "$STOCK.tmp" "$STOCK"
  fi
  echo "Saved connected stock XBL as $STOCK"
else
  mkdir -p "$STOCK_BACKUPS"
  observed="$STOCK_BACKUPS/xbl-observed-dragon-q6a-${current_hash:0:8}.bin"
  if [ ! -f "$observed" ]; then
    cp "$CURRENT" "$observed.tmp"
    mv "$observed.tmp" "$observed"
  fi
  echo "ERROR: connected XBL $current_hash is not a validated stock revision" >&2
  echo "It was preserved at $observed; no firmware was written." >&2
  echo "For attended bring-up, set VAMOS_XBL_STOCK=$observed and VAMOS_ALLOW_UNTESTED_XBL=1." >&2
  exit 1
fi

if [ ! -f "$STOCK" ]; then
  echo "ERROR: no stock XBL backup is available: $STOCK" >&2
  echo "Set VAMOS_XBL_STOCK to this Dragon's verified backup." >&2
  exit 1
fi
if [ "$(stat -c%s "$STOCK")" -ne "$XBL_SIZE" ]; then
  echo "ERROR: stock XBL backup is not exactly 6 MiB: $STOCK" >&2
  exit 1
fi
stock_hash="$(sha256sum "$STOCK" | cut -d' ' -f1)"

if [ "$restore" -eq 1 ]; then
  payload="$STOCK"
else
  if ! stock_is_supported "$stock_hash" && \
     [ "${VAMOS_ALLOW_UNTESTED_XBL:-0}" != 1 ]; then
    echo "ERROR: U-Boot is not validated with stock XBL $stock_hash" >&2
    echo "The backup was preserved at $STOCK; keep stock UEFI on this SOM." >&2
    echo "Set VAMOS_ALLOW_UNTESTED_XBL=1 only for an attended recovery test." >&2
    exit 1
  fi
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
  python3 "$QTESTSIGN" -v6 sbl1 "$UNSIGNED" -o "$SIGNED"
  if [ "$(stat -c%s "$SIGNED")" -gt "$XBL_SIZE" ]; then
    echo "ERROR: signed XBL is larger than 6 MiB: $SIGNED" >&2
    exit 1
  fi
  # qtestsign emits through the final ELF segment, while the SPI partition
  # also contains device-specific trailing bytes. Produce one deterministic,
  # full-partition payload so verification covers every programmed byte.
  cp "$STOCK" "$UBOOT"
  dd if="$SIGNED" of="$UBOOT" conv=notrunc status=none

  # Firehose rounds writes up to the target's 4 KiB logical block size. Match
  # that behavior so stock bytes cannot leak into the signed ELF's padding.
  signed_size=$(stat -c%s "$SIGNED")
  aligned_size=$(( (signed_size + 4095) / 4096 * 4096 ))
  dd if=/dev/zero of="$UBOOT" bs=1 seek="$signed_size" \
    count=$((aligned_size - signed_size)) conv=notrunc status=none
  payload="$UBOOT"
fi
if [ "$(stat -c%s "$payload")" -ne "$XBL_SIZE" ]; then
  echo "ERROR: XBL payload is not exactly 6 MiB: $payload" >&2
  exit 1
fi

echo "== Writing XBL: $(basename "$payload") =="
"${EDL[@]}" write-part XBL "$payload"
"${EDL[@]}" read-part XBL "$VERIFY"

payload_hash="$(sha256sum "$VERIFY" | cut -d' ' -f1)"
expected_hash="$(sha256sum "$payload" | cut -d' ' -f1)"
if [ "$payload_hash" != "$expected_hash" ]; then
  echo "ERROR: XBL readback does not match payload" >&2
  exit 1
fi
echo "Verified XBL payload sha256 $expected_hash"

echo "== Resetting Dragon =="
"${EDL[@]}" reset
