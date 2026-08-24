#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
UBOOT="$DIR/bootloader/u-boot"
QTESTSIGN="$DIR/bootloader/qtestsign/qtestsign.py"
OUT="$DIR/build/u-boot"
CONFIG_FRAGMENT="$DIR/bootloader/configs/dragon-q6a.config"
UBOOT_PATCH_DIR="$DIR/bootloader/patches/u-boot"
QTESTSIGN_PATCH_DIR="$DIR/bootloader/patches/qtestsign"
UNSIGNED="$OUT/u-boot.elf"
CORE="$OUT/u-boot.bin"
XBL_CORE="$OUT/u-boot-xbl-core.bin"
# Size of the stock EDK2 load segment embedded in the Dragon Q6A XBL.
XBL_CORE_SIZE=$((0x374000))

for input in "$UBOOT/Makefile" "$QTESTSIGN" "$CONFIG_FRAGMENT"; do
  if [ ! -e "$input" ]; then
    echo "ERROR: missing $input" >&2
    echo "Run: git submodule update --init bootloader/u-boot bootloader/qtestsign" >&2
    exit 1
  fi
done

mkdir -p "$OUT"

apply_patch_set() {
  local tree="$1"
  local patch_dir="$2"
  local patch

  for patch in "$patch_dir"/*.patch; do
    [ -e "$patch" ] || continue
    if git -C "$tree" apply --reverse --check "$patch" >/dev/null 2>&1; then
      continue
    fi
    echo "Applying $(basename "$patch")"
    git -C "$tree" apply --check "$patch"
    git -C "$tree" apply "$patch"
  done
}

apply_patch_set "$UBOOT" "$UBOOT_PATCH_DIR"
apply_patch_set "$DIR/bootloader/qtestsign" "$QTESTSIGN_PATCH_DIR"

echo "== Configuring Dragon Q6A U-Boot =="
make -C "$UBOOT" O="$OUT" CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}" qcm6490_defconfig
"$UBOOT/scripts/kconfig/merge_config.sh" -m -O "$OUT" "$OUT/.config" "$CONFIG_FRAGMENT"
make -C "$UBOOT" O="$OUT" CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}" olddefconfig

echo "== Building Dragon Q6A U-Boot =="
make -C "$UBOOT" O="$OUT" CROSS_COMPILE="${CROSS_COMPILE:-aarch64-linux-gnu-}" -j"$(nproc)"

core_size="$(stat -c%s "$CORE")"
if [ "$core_size" -gt "$XBL_CORE_SIZE" ]; then
  echo "ERROR: U-Boot core is $core_size bytes; stock XBL core is $XBL_CORE_SIZE bytes" >&2
  exit 1
fi

# XBL executes the segment base. The stock core's first instruction is a
# branch from 0x9f000000 to its reset vector, so preserve the same contract.
cp "$CORE" "$XBL_CORE"

sha256sum "$UNSIGNED" "$CORE" "$XBL_CORE"
echo "Built $XBL_CORE ($core_size / $XBL_CORE_SIZE bytes, entry +0x0)"
