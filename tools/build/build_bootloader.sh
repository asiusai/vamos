#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
BUILD_DIR="$DIR/build"
OUTPUT="$BUILD_DIR/BOOTAA64.EFI"
EDL_OUTPUT="$BUILD_DIR/EDLAA64.EFI"
CONFIG="$DIR/tools/boot/grub.cfg"
EDL_SOURCE="$DIR/tools/boot/edl_efi.S"

GRUB_PACKAGE_VERSION="2.12-1ubuntu7.3"
GRUB_PACKAGE_SHA256="d43219525621449cd795dd0a932f28729ea7da391115d84436c90521ab30ed31"
GRUB_PACKAGE_URL="https://launchpad.net/ubuntu/+archive/primary/+files/grub-efi-arm64-bin_${GRUB_PACKAGE_VERSION}_arm64.deb"
GRUB_CACHE="$BUILD_DIR/grub-arm64"

if ! command -v grub-mkstandalone >/dev/null 2>&1; then
  echo "ERROR: grub-mkstandalone is required (Ubuntu package: grub2-common)" >&2
  exit 1
fi

package="$GRUB_CACHE/grub-efi-arm64-bin.deb"
GRUB_MODULES="$GRUB_CACHE/root/usr/lib/grub/arm64-efi"
mkdir -p "$GRUB_CACHE/root"
if [ ! -f "$package" ] || ! echo "$GRUB_PACKAGE_SHA256  $package" | sha256sum --check --status; then
  echo "== Downloading pinned Ubuntu ARM64 GRUB modules =="
  curl --fail --location --retry 3 --output "$package.tmp" "$GRUB_PACKAGE_URL"
  echo "$GRUB_PACKAGE_SHA256  $package.tmp" | sha256sum --check --status
  mv "$package.tmp" "$package"
fi
if [ ! -f "$GRUB_MODULES/modinfo.sh" ]; then
  if command -v dpkg-deb >/dev/null 2>&1; then
    dpkg-deb --extract "$package" "$GRUB_CACHE/root"
  elif command -v ar >/dev/null 2>&1 && command -v bsdtar >/dev/null 2>&1; then
    data_member="$(ar t "$package" | awk '/^data\.tar/{print; exit}')"
    [ -n "$data_member" ] || { echo "ERROR: Debian package has no data archive" >&2; exit 1; }
    ar p "$package" "$data_member" | bsdtar -xf - -C "$GRUB_CACHE/root"
  else
    echo "ERROR: extracting GRUB requires dpkg-deb or ar and bsdtar" >&2
    exit 1
  fi
fi

mkdir -p "$BUILD_DIR"
if command -v aarch64-linux-gnu-gcc >/dev/null 2>&1; then
  EFI_CC=aarch64-linux-gnu-gcc
  EFI_LD=aarch64-linux-gnu-ld
  EFI_OBJCOPY=aarch64-linux-gnu-objcopy
elif [ "$(uname -m)" = aarch64 ]; then
  EFI_CC=gcc
  EFI_LD=ld
  EFI_OBJCOPY=objcopy
else
  echo "ERROR: an AArch64 GNU toolchain is required" >&2
  exit 1
fi

echo "== Building stock-UEFI software EDL application =="
"$EFI_CC" -c -ffreestanding -fno-stack-protector -fno-pic \
  -o "$BUILD_DIR/edl-efi.o" "$EDL_SOURCE"
"$EFI_LD" -nostdlib -e efi_main -Ttext=0x1000 --section-start=.reloc=0x2000 \
  -o "$BUILD_DIR/edl-efi.elf" "$BUILD_DIR/edl-efi.o"
"$EFI_OBJCOPY" -O pei-aarch64-little --subsystem=efi-app \
  "$BUILD_DIR/edl-efi.elf" "$EDL_OUTPUT.tmp"
mv "$EDL_OUTPUT.tmp" "$EDL_OUTPUT"

echo "== Building disk-resident ARM64 A/B selector =="
grub-mkstandalone \
  --directory="$GRUB_MODULES" \
  --format=arm64-efi \
  --output="$OUTPUT.tmp" \
  --compress=xz \
  --locales="" \
  --fonts="" \
  --install-modules="part_gpt fat search search_label loadenv linux chain normal configfile test echo regexp fdt halt" \
  --modules="part_gpt fat search search_label loadenv linux chain regexp fdt" \
  "boot/grub/grub.cfg=$CONFIG"
mv "$OUTPUT.tmp" "$OUTPUT"

file "$OUTPUT"
file "$EDL_OUTPUT"
