#!/bin/sh
set -eu

QCOM_ADRENO_LIB_DIR="${QCOM_ADRENO_LIB_DIR:-/opt/qcom-adreno/lib}"
QCOM_OPENCL="$QCOM_ADRENO_LIB_DIR/libOpenCL.so.1.0.0"

[ -d "$QCOM_ADRENO_LIB_DIR" ] || exit 0
[ -e "$QCOM_OPENCL" ] || exit 0

mkdir -p /etc/ld.so.conf.d
printf '%s\n' "$QCOM_ADRENO_LIB_DIR" > /etc/ld.so.conf.d/00-qcom-adreno.conf

# Some OpenCL callers still resolve the distro ICD loader from /usr/lib before
# the loader cache, so make the default OpenCL loader the Qualcomm one.
for lib in libOpenCL.so libOpenCL.so.1 libOpenCL.so.1.0.0; do
  [ -e "$QCOM_ADRENO_LIB_DIR/$lib" ] || continue
  ln -snf "$QCOM_ADRENO_LIB_DIR/$lib" "/usr/lib/$lib"
done

if command -v ldconfig >/dev/null 2>&1; then
  ldconfig
fi
