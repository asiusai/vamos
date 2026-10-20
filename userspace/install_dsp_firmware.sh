#!/bin/bash
set -euo pipefail

# Install the pinned DSP firmware and Asius audio topology.

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)
. "$script_dir/dsp_firmware.sh"

fetch() {
  local name=$1 url=$2 expected=$3
  curl --fail --location --silent --show-error --output "$work_dir/$name" "$url"
  printf '%s  %s\n' "$expected" "$work_dir/$name" | sha256sum --check --status
}

extract_deb() {
  local name=$1 destination=$2
  mkdir -p "$destination"
  ar p "$work_dir/$name" data.tar.xz | tar -xJf - -C "$destination"
}

fetch firmware.deb \
  "$DSP_FIRMWARE_URL" "$DSP_FIRMWARE_SHA256"

extract_deb firmware.deb "$work_dir/firmware"

firmware_boot="$work_dir/firmware/lib/firmware/qcom/qcs6490/radxa/dragon-q6a"
audio_topology="$script_dir/audio/QCS6490-Asius-Panda-tplg.bin"

install -d /lib/firmware/qcom/qcs6490/radxa/dragon-q6a
for firmware in adsp.mbn adspr.jsn adspua.jsn cdsp.mbn cdspr.jsn; do
  install -m644 "$firmware_boot/$firmware" \
    "/lib/firmware/qcom/qcs6490/radxa/dragon-q6a/$firmware"
done
install -m644 "$audio_topology" \
  /lib/firmware/qcom/qcs6490/radxa/dragon-q6a/QCS6490-Radxa-Dragon-Q6A-tplg.bin
ln -sfn radxa/dragon-q6a/QCS6490-Radxa-Dragon-Q6A-tplg.bin \
  /lib/firmware/qcom/qcs6490/QCS6490-Radxa-Dragon-Q6A-tplg.bin

install -d /usr/share/licenses/radxa-firmware
install -m644 "$work_dir/firmware/usr/share/doc/radxa-firmware-qcs6490/copyright" /usr/share/licenses/radxa-firmware/
