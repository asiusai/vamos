#!/bin/bash
set -e
DIR=/home/john/asius/vamos
export ARCH=arm64
export CROSS_COMPILE=aarch64-none-elf-
export KCFLAGS="-w"
CC_CMD="ccache aarch64-none-elf-gcc"
make CC="$CC_CMD" -C "$DIR/kernel/linux" O="$DIR/build/kernel-out" M="$DIR/kernel/modules/camera-overlay" ARCH=arm64 CROSS_COMPILE=aarch64-none-elf- modules
