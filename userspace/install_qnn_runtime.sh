#!/bin/bash
set -euo pipefail

# Install the pinned v68 HTP runtime. The optional root supports staging checks.
root=${1:-/}
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

fetch_deb() {
  local name=$1 url=$2 expected=$3
  curl --fail --location --silent --show-error --output "$work_dir/$name.deb" "$url"
  printf '%s  %s\n' "$expected" "$work_dir/$name.deb" | sha256sum --check --status
  mkdir -p "$work_dir/$name"
  ar p "$work_dir/$name.deb" data.tar.xz | tar -xJf - -C "$work_dir/$name"
}

fetch_deb qnn \
  https://radxa-repo.github.io/qcs6490-noble/pool/main/q/qcom-qairt/qcom-qnn-sdk-v68_0.1.0-2_all.deb \
  306fd647b88902d170831980ca68a31d77d77923534e2070783fbeca132a6e54
fetch_deb fastrpc \
  https://github.com/radxa-pkg/fastrpc/releases/download/1.0.7-2/libcdsprpc1_1.0.7-2_arm64.deb \
  979b41e063674ed8e86ea4b7da40f15409758bb71731e9b7b28f9b0210ed9f3a
fetch_deb listener \
  https://github.com/radxa-pkg/fastrpc/releases/download/1.0.7-2/libcdsp-default-listener1_1.0.7-2_arm64.deb \
  bacc3d6c0ed67ecac2a37038f1fde3314f319abefad8d7a48bb7ad5f8553cb1a

install -d "$root/usr/lib" "$root/usr/lib/dsp" "$root/usr/bin" "$root/usr/share/licenses/qnn"
for library in libQnnHtp.so libQnnHtpPrepare.so libQnnHtpV68Stub.so libQnnSystem.so \
               libQnnHtpProfilingReader.so libQnnHtpNetRunExtensions.so; do
  install -m644 "$work_dir/qnn/usr/lib/aarch64-linux-gnu/$library" "$root/usr/lib/$library"
done
install -m644 "$work_dir/qnn/usr/lib/aarch64-linux-gnu/libQnnHtpV68Skel.so" "$root/usr/lib/dsp/"
install -m644 "$work_dir/fastrpc/usr/lib/aarch64-linux-gnu/libcdsprpc.so.1.0.0" "$root/usr/lib/"
install -m644 "$work_dir/listener/usr/lib/aarch64-linux-gnu/libcdsp_default_listener.so.1.0.0" "$root/usr/lib/"
for library in libcdsprpc libcdsp_default_listener; do
  ln -sfn "$library.so.1.0.0" "$root/usr/lib/$library.so"
  ln -sfn "$library.so.1.0.0" "$root/usr/lib/$library.so.1"
done
for tool in qnn-net-run qnn-profile-viewer qnn-context-binary-generator qnn-context-binary-utility; do
  install -m755 "$work_dir/qnn/usr/bin/$tool" "$root/usr/bin/"
done
for package in qnn fastrpc listener; do
  cp -a "$work_dir/$package/usr/share/doc" "$root/usr/share/licenses/qnn/$package"
done
