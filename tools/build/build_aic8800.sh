#!/usr/bin/env bash
# Run inside vamos-builder after the kernel and its Module.symvers are built.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE_DIR="$DIR/kernel/aic8800"
AIC_BUILD_DIR="$DIR/build/aic8800"
KBUILD_OUT="$DIR/build/kernel-out"

test -s "$KBUILD_OUT/Module.symvers"
test -f "$SOURCE_DIR/debian/patches/series"

# Keep vendor sources and their upstream patch series untouched. Only this
# disposable build copy is normalized/patched, matching Radxa's packaging.
rm -rf "$AIC_BUILD_DIR"
mkdir -p "$AIC_BUILD_DIR"
git -C "$SOURCE_DIR" archive HEAD src debian/patches | tar -x -C "$AIC_BUILD_DIR"
python3 - "$AIC_BUILD_DIR/src" <<'PY'
from pathlib import Path
import sys

for path in Path(sys.argv[1]).rglob("*"):
  if path.is_file() and (path.suffix in (".c", ".h") or path.name in ("Makefile", "Kconfig")):
    content = path.read_bytes()
    if b"\r\n" in content:
      path.write_bytes(content.replace(b"\r\n", b"\n"))
PY
while IFS= read -r patch; do
  [[ -z "$patch" || "$patch" == \#* ]] && continue
  patch --batch --forward --fuzz=0 -d "$AIC_BUILD_DIR" -p1 \
    -i "$AIC_BUILD_DIR/debian/patches/$patch"
done < "$SOURCE_DIR/debian/patches/series"

export ARCH=arm64
export CCACHE_DIR="${CCACHE_DIR:-$DIR/.ccache}"
export LOCALVERSION=-vamos
if [[ "$(uname -m)" != aarch64 && "$(uname -m)" != arm64 ]]; then
  export CROSS_COMPILE="${CROSS_COMPILE:-aarch64-none-elf-}"
fi
make_args=(
  -C "$DIR/kernel/linux" O="$KBUILD_OUT"
  M="$AIC_BUILD_DIR/src/USB/driver_fw/drivers/aic8800"
  CC="ccache ${CROSS_COMPILE:-}gcc"
)
make -j"${VAMOS_BUILD_JOBS:-$(nproc)}" "${make_args[@]}" modules
make "${make_args[@]}" INSTALL_MOD_PATH="$DIR/build/modules_install" \
  INSTALL_MOD_DIR=extra INSTALL_MOD_STRIP=1 modules_install
