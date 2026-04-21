#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
cd "$DIR"

tools/bin/qdl flash system $DIR/build/system.erofs.img

if [ "${VAMOS_NO_RESET:-}" != "1" ]; then
  tools/bin/qdl reset
fi
