#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
BUILD_DIR="$DIR/build"
OUTPUT="$BUILD_DIR/BOOTAA64.EFI"
CONFIG="$DIR/tools/boot/grub.cfg"

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
echo "== Building disk-resident ARM64 A/B selector =="
grub-mkstandalone \
  --directory="$GRUB_MODULES" \
  --format=arm64-efi \
  --output="$OUTPUT.tmp" \
  --compress=xz \
  --locales="" \
  --fonts="" \
  --install-modules="part_gpt fat search search_label loadenv chain normal configfile test echo regexp halt" \
  --modules="part_gpt fat search search_label loadenv chain regexp" \
  "boot/grub/grub.cfg=$CONFIG"
mv "$OUTPUT.tmp" "$OUTPUT"

file "$OUTPUT"
