#!/usr/bin/env bash
set -euo pipefail

DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)
UPSTREAM_URL=https://github.com/linux-msm/audioreach-topology.git
UPSTREAM_TAG=v1.0.4
UPSTREAM_COMMIT=d7a5e9d80ad18a7a6844eeb32cacbdeea0e7e677
EXPECTED_SHA256=f49f942604fcfe6601ea064aaeb196db6ad863def58ef7ad4efc0a763ace62b5

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

git clone --quiet --depth 1 --branch "$UPSTREAM_TAG" \
  "$UPSTREAM_URL" "$work_dir/audioreach-topology"
actual_commit=$(git -C "$work_dir/audioreach-topology" rev-parse HEAD)
if [ "$actual_commit" != "$UPSTREAM_COMMIT" ]; then
  echo "ERROR: $UPSTREAM_TAG resolved to unexpected commit $actual_commit" >&2
  exit 1
fi

m4 -I "$work_dir/audioreach-topology" \
  "$DIR/QCS6490-Asius-Panda.m4" > "$work_dir/QCS6490-Asius-Panda.conf"
alsatplg -c "$work_dir/QCS6490-Asius-Panda.conf" \
  -o "$work_dir/QCS6490-Asius-Panda-tplg.bin"

printf '%s  %s\n' "$EXPECTED_SHA256" \
  "$work_dir/QCS6490-Asius-Panda-tplg.bin" | sha256sum --check --status
install -m644 "$work_dir/QCS6490-Asius-Panda-tplg.bin" \
  "$DIR/QCS6490-Asius-Panda-tplg.bin"
sha256sum "$DIR/QCS6490-Asius-Panda-tplg.bin"
