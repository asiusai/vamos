#!/bin/bash
set -euo pipefail

# The QCS6490 cDSP firmware, FastRPC userspace, and QAIRT libraries must be a
# matched set. Keep these URLs and hashes pinned so a vamOS rebuild cannot pick
# up an incompatible repository update.
QCS_REPO=https://radxa-repo.github.io/qcs6490-noble/pool/main
RADXA_REPO=https://radxa-repo.github.io/noble/pool/main

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)

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

fetch qnn.deb \
  "$QCS_REPO/q/qcom-qairt/qcom-qnn-sdk-v68_0.1.0-2_all.deb" \
  306fd647b88902d170831980ca68a31d77d77923534e2070783fbeca132a6e54
fetch fastrpc.deb \
  "$QCS_REPO/f/fastrpc/fastrpc_1.0.7-2_arm64.deb" \
  b14baf0800c934f4ca1d01f0ddded60c9c939fffff3817ec086eb2c2fc739b2c
fetch libcdsprpc.deb \
  "$QCS_REPO/f/fastrpc/libcdsprpc1_1.0.7-2_arm64.deb" \
  979b41e063674ed8e86ea4b7da40f15409758bb71731e9b7b28f9b0210ed9f3a
fetch listener.deb \
  "$QCS_REPO/f/fastrpc/libcdsp-default-listener1_1.0.7-2_arm64.deb" \
  bacc3d6c0ed67ecac2a37038f1fde3314f319abefad8d7a48bb7ad5f8553cb1a
fetch fastrpc-test.deb \
  "$QCS_REPO/f/fastrpc/fastrpc-test_1.0.7-2_arm64.deb" \
  884869fd8cd8b502c12f2d1f83f219c36800ac18eae091dfbf1dbf96bc8ae02a
fetch firmware.deb \
  "$RADXA_REPO/r/radxa-firmware/radxa-firmware-qcs6490_0.2.41_all.deb" \
  8d53cc8da941bf748f3cd1fe7086da5fda73c680a6ffa96bfb77d95c9f1d9604

for package in qnn fastrpc libcdsprpc listener fastrpc-test firmware; do
  extract_deb "$package.deb" "$work_dir/$package"
done

qnn_lib="$work_dir/qnn/usr/lib/aarch64-linux-gnu"
fastrpc_lib="$work_dir/libcdsprpc/usr/lib/aarch64-linux-gnu"
listener_lib="$work_dir/listener/usr/lib/aarch64-linux-gnu"
firmware_dsp="$work_dir/firmware/usr/share/qcom/qcs6490/radxa/dragon-q6a/dsp/cdsp"
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

install -Dm644 "$fastrpc_lib/libcdsprpc.so.1.0.0" /usr/lib/libcdsprpc.so.1.0.0
ln -sf libcdsprpc.so.1.0.0 /usr/lib/libcdsprpc.so.1
ln -sf libcdsprpc.so.1 /usr/lib/libcdsprpc.so
install -Dm644 "$listener_lib/libcdsp_default_listener.so.1.0.0" /usr/lib/libcdsp_default_listener.so.1.0.0
ln -sf libcdsp_default_listener.so.1.0.0 /usr/lib/libcdsp_default_listener.so.1

for library in \
  libQnnHtp.so \
  libQnnHtpPrepare.so \
  libQnnHtpProfilingReader.so \
  libQnnHtpV68Stub.so \
  libQnnSystem.so; do
  install -Dm644 "$qnn_lib/$library" "/usr/lib/$library"
done

# FastRPC searches this canonical directory for the unsigned shell before it
# considers ADSP_LIBRARY_PATH. Without it, a cold boot fails with 0x80000600.
install -d /usr/lib/dsp
for library in fastrpc_shell_unsigned_3 libc++.so.1 libc++abi.so.1; do
  install -m644 "$firmware_dsp/$library" "/usr/lib/dsp/$library"
done
install -m644 "$qnn_lib/libQnnHtpV68Skel.so" /usr/lib/dsp/libQnnHtpV68Skel.so
install -m644 "$work_dir/fastrpc-test/usr/share/fastrpc_test/v68/libcalculator_skel.so" /usr/lib/dsp/libcalculator_skel.so

install -Dm755 "$work_dir/fastrpc-test/usr/bin/fastrpc_test" /usr/bin/fastrpc_test
install -Dm644 "$work_dir/fastrpc-test/usr/lib/fastrpc_test/libcalculator.so" /usr/lib/fastrpc_test/libcalculator.so

install -d /usr/share/licenses/qairt /usr/share/licenses/fastrpc /usr/share/licenses/radxa-firmware
install -m644 "$work_dir/qnn/usr/share/doc/qcom-qnn-sdk-v68/LICENSE.pdf.gz" /usr/share/licenses/qairt/
install -m644 "$work_dir/qnn/usr/share/doc/qcom-qnn-sdk-v68/copyright" /usr/share/licenses/qairt/
install -m644 "$work_dir/fastrpc/usr/share/doc/fastrpc/copyright" /usr/share/licenses/fastrpc/
install -m644 "$work_dir/firmware/usr/share/doc/radxa-firmware-qcs6490/copyright" /usr/share/licenses/radxa-firmware/

ldconfig
