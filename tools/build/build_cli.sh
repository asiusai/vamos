#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
mkdir -p build/cli
LIBUSB="$(ldconfig -p | awk '$1 == "libusb-1.0.so.0" && /x86-64/ {path=$NF} END {print path}')"
test -n "$LIBUSB" || { echo 'Install libusb-1.0-0 to build the standalone flasher.' >&2; exit 1; }
python3 -m PyInstaller --noconfirm --clean --onefile --name vamos \
  --paths "$ROOT" --distpath build/cli --workpath build/cli/work --specpath build/cli \
  --add-binary "$ROOT/tools/bin/edl-ng-dist/edl-ng:tools/bin/edl-ng-dist" \
  --add-binary "$ROOT/tools/bin/edl-ng-dist/libSystem.IO.Ports.Native.so:tools/bin/edl-ng-dist" \
  --add-binary "$LIBUSB:." \
  --add-data "$ROOT/userspace/root/usr/share/vamos/update-public.pem:userspace/root/usr/share/vamos" \
  --add-data "$ROOT/tools/bin/edl-ng-dist/LICENSE:tools/bin/edl-ng-dist" \
  tools/flash/cli.py
build/cli/vamos --help
build/cli/vamos devices
tar -C build/cli -czf build/cli/vamos-linux-x86_64.tar.gz vamos
(cd build/cli && sha256sum vamos-linux-x86_64.tar.gz > SHA256SUMS)
