#!/usr/bin/env bash
# Rebuild the shipped Linux x64 tool from pinned upstream source.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOTNET="${DOTNET:-dotnet}"
if [ "$("$DOTNET" --version)" != 9.0.319 ]; then
  echo "Use .NET SDK 9.0.319. Set DOTNET to its executable." >&2
  exit 1
fi
SOURCE="$(mktemp -d)"
trap 'rm -rf "$SOURCE"' EXIT
git -C "$SOURCE" init -q
git -C "$SOURCE" remote add origin https://github.com/strongtz/edl-ng.git
git -C "$SOURCE" fetch --depth=1 origin 78f2078da4e4dc563daa7366f16bfb7015193966
git -C "$SOURCE" checkout --detach -q FETCH_HEAD
git -C "$SOURCE" apply "$HERE/session.patch"
cp "$HERE/cli.lock.json" "$SOURCE/QCEDL.CLI/packages.lock.json"
cp "$HERE/library.lock.json" "$SOURCE/QCEDL.NET/packages.lock.json"
cp "$HERE/analyzer.lock.json" "$SOURCE/QCEDL.Analyzer/packages.lock.json"
export DOTNET_CLI_TELEMETRY_OPTOUT=1
"$DOTNET" restore "$SOURCE/QCEDL.CLI" -r linux-x64 --locked-mode
"$DOTNET" publish "$SOURCE/QCEDL.CLI" -c Release -r linux-x64 --no-restore -o "$SOURCE/output"
DESTINATION="$HERE/../../bin/edl-ng-dist"
install -m755 "$SOURCE/output/edl-ng" "$DESTINATION/edl-ng"
install -m755 "$SOURCE/output/libSystem.IO.Ports.Native.so" "$DESTINATION/libSystem.IO.Ports.Native.so"
