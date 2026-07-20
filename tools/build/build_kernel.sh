#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
cd "$DIR"

KERNEL_DIR="$DIR/kernel/linux"
PATCHES_DIR="$DIR/kernel/patches"
KBUILD_OUT="$DIR/build/kernel-out"
OUT_DIR="$DIR/build"

BASE_DEFCONFIG="defconfig"
CONFIG_FRAGMENT="$DIR/kernel/configs/vamos.config"

# Dragon Q6A DTB, added to the kernel tree by the Dragon patch series.
DTB_TARGET="qcom/qcs6490-radxa-dragon-q6a.dtb"

HOST_OS="$(uname)"
KERNEL_LINUX_VOLUME="vamos-kernel-linux"
CCACHE_VOLUME="vamos-kernel-ccache"
CONTAINER_ID=""

prepare_kernel_volume() {
  docker volume create "$KERNEL_LINUX_VOLUME" >/dev/null
  docker run --rm \
    --entrypoint sh \
    -v "$KERNEL_LINUX_VOLUME:/linux" \
    vamos-builder \
    -lc "mkdir -p /linux && chown $(id -u):$(id -g) /linux && chmod 0775 /linux"
}

seed_kernel_workspace() {
  local sync_container_id
  local kernel_bundle="$DIR/build/kernel-linux.bundle"

  echo "Syncing kernel/linux into Docker volume"
  mkdir -p "$DIR/build"
  rm -f "$kernel_bundle"
  git -C "$KERNEL_DIR" bundle create "$kernel_bundle" HEAD >/dev/null

  sync_container_id=$(docker run -d --entrypoint tail -v "$kernel_bundle:/kernel-linux.bundle:ro" -v "$KERNEL_LINUX_VOLUME:/linux" vamos-builder -f /dev/null)
  docker exec "$sync_container_id" sh -lc "rm -rf /linux/* /linux/.[!.]* /linux/..?*"
  docker exec -u "$(id -u):$(id -g)" "$sync_container_id" sh -lc "cd /linux && git clone /kernel-linux.bundle . >/dev/null 2>&1 && git checkout --force '$KERNEL_REV' >/dev/null 2>&1"
  docker container rm -f "$sync_container_id" >/dev/null
  rm -f "$kernel_bundle"
}

prepare_ccache_volume() {
  if ! docker volume inspect "$CCACHE_VOLUME" >/dev/null 2>&1; then
    docker volume create "$CCACHE_VOLUME" >/dev/null
    docker run --rm \
      --entrypoint sh \
      -v "$CCACHE_VOLUME:/ccache" \
      vamos-builder \
      -lc "mkdir -p /ccache && chown $(id -u):$(id -g) /ccache && chmod 0775 /ccache"
  fi
}

kernel_workspace_ready() {
  docker volume inspect "$KERNEL_LINUX_VOLUME" >/dev/null 2>&1 || return 1
  docker run --rm --entrypoint sh -v "$KERNEL_LINUX_VOLUME:/linux" vamos-builder \
    -lc "test \"\$(git -c safe.directory=/linux -C /linux rev-parse HEAD 2>/dev/null)\" = \"$KERNEL_REV\"" \
    >/dev/null
}

if [ ! -f "$KERNEL_DIR/Makefile" ]; then
  "$DIR/vamos" setup
fi

KERNEL_REV="$(git -C "$KERNEL_DIR" rev-parse HEAD)"

echo "Building vamos-builder docker image"
export DOCKER_BUILDKIT=1
docker build -f tools/build/Dockerfile.builder -t vamos-builder "$DIR" \
  --build-arg UNAME="$(id -nu)" \
  --build-arg UID="$(id -u)" \
  --build-arg GID="$(id -g)"

echo "Starting vamos-builder container"
if [ "$HOST_OS" = "Darwin" ]; then
  if ! kernel_workspace_ready; then
    echo "Kernel workspace volume is missing, uninitialized, or out of date; reseeding"
    prepare_kernel_volume
    seed_kernel_workspace
  fi
  prepare_ccache_volume
  CONTAINER_ID=$(docker run -d \
    --ulimit nofile=65536:65536 \
    -u "$(id -u):$(id -g)" \
    -v "$DIR":"$DIR" \
    -v "$KERNEL_LINUX_VOLUME:$KERNEL_DIR" \
    -v "$CCACHE_VOLUME:/ccache" \
    -w "$DIR" \
    vamos-builder)
else
  GIT_MOUNT_ARGS=()
  if [ -f "$DIR/.git" ]; then
    GIT_COMMON_DIR="$(git -C "$DIR" rev-parse --path-format=absolute --git-common-dir)"
    GIT_MOUNT_ARGS=(-v "$GIT_COMMON_DIR":"$GIT_COMMON_DIR")
  fi
  CONTAINER_ID=$(docker run -d \
    --ulimit nofile=65536:65536 \
    -u "$(id -u):$(id -g)" \
    -v "$DIR":"$DIR" \
    "${GIT_MOUNT_ARGS[@]}" \
    -w "$DIR" \
    vamos-builder)
fi

trap cleanup EXIT

clean_kernel_tree() {
  git -C "$KERNEL_DIR" reset --hard HEAD >/dev/null 2>&1 || true
  git -C "$KERNEL_DIR" clean -fd >/dev/null 2>&1 || true
}

apply_patches() {
  cd "$KERNEL_DIR"

  echo "-- Resetting kernel submodule to clean state --"
  clean_kernel_tree

  if [ -d "$PATCHES_DIR" ] && ls "$PATCHES_DIR"/*.patch 1>/dev/null 2>&1; then
    echo "-- Applying patches --"
    for patch in "$PATCHES_DIR"/*.patch; do
      echo "Applying $(basename "$patch")"
      git apply --check --whitespace=nowarn "$patch"
      git apply --whitespace=nowarn "$patch"
    done
  fi
}

build_kernel() {
  apply_patches

  local arch_host
  arch_host=$(uname -m)
  export ARCH=arm64
  if [ "$arch_host" != "aarch64" ] && [ "$arch_host" != "arm64" ]; then
    export CROSS_COMPILE=aarch64-none-elf-
  fi

  if [ "$HOST_OS" = "Darwin" ]; then
    export CCACHE_DIR="/ccache"
  else
    export CCACHE_DIR="$DIR/.ccache"
  fi
  local make_args=(
    O="$KBUILD_OUT"
    CC="ccache ${CROSS_COMPILE:-}gcc"
    HOSTCC="ccache gcc"
    HOSTCXX="ccache g++"
  )

  export KBUILD_BUILD_USER="vamos"
  export KBUILD_BUILD_HOST="vamos"
  export KCFLAGS="-w"
  export LOCALVERSION="-vamos"

  cd "$KERNEL_DIR"
  mkdir -p "$KBUILD_OUT"

  echo "-- Loading base config $BASE_DEFCONFIG --"
  make "${make_args[@]}" "$BASE_DEFCONFIG"

  echo "-- Merging config fragment $(basename "$CONFIG_FRAGMENT") --"
  KCONFIG_CONFIG="$KBUILD_OUT/.config" \
    bash scripts/kconfig/merge_config.sh \
    -m -y "$KBUILD_OUT/.config" "$CONFIG_FRAGMENT"
  echo "CONFIG_EXTRA_FIRMWARE_DIR=\"$DIR/kernel/firmware\"" >> "$KBUILD_OUT/.config"
  make "${make_args[@]}" olddefconfig

  echo "-- Building kernel with $(nproc) cores --"
  make -j"$(nproc)" "${make_args[@]}" Image Image.gz "$DTB_TARGET"

  echo "-- Building and installing kernel modules --"
  make -j"$(nproc)" "${make_args[@]}" modules
  rm -rf "$OUT_DIR/modules_install"
  make "${make_args[@]}" INSTALL_MOD_PATH="$OUT_DIR/modules_install" modules_install

  mkdir -p "$OUT_DIR"
  cp "$KBUILD_OUT/arch/arm64/boot/Image" "$OUT_DIR/Image"
  cp "$KBUILD_OUT/arch/arm64/boot/Image.gz" "$OUT_DIR/Image.gz"
  cp "$KBUILD_OUT/arch/arm64/boot/dts/$DTB_TARGET" "$OUT_DIR/$(basename "$DTB_TARGET")"

  echo "-- Done --"
  ls -lh "$OUT_DIR/Image" "$OUT_DIR/Image.gz" "$OUT_DIR/$(basename "$DTB_TARGET")"
}

cleanup() {
  echo "Cleaning up container and kernel tree..."

  if [ "$HOST_OS" = "Darwin" ]; then
    docker exec -i -u "$(id -u):$(id -g)" "$CONTAINER_ID" bash >/dev/null 2>&1 <<EOF || true
$(declare -f clean_kernel_tree)
KERNEL_DIR='$KERNEL_DIR'
clean_kernel_tree
EOF
  else
    clean_kernel_tree
  fi

  docker container rm -f "${CONTAINER_ID:-}" >/dev/null 2>&1 || true
}

docker exec -i -u "$(id -u):$(id -g)" "$CONTAINER_ID" bash <<EOF
set -e

HOST_OS='$HOST_OS'
BASE_DEFCONFIG='$BASE_DEFCONFIG'
CONFIG_FRAGMENT='$CONFIG_FRAGMENT'
DTB_TARGET='$DTB_TARGET'
DIR='$DIR'
KERNEL_DIR='$KERNEL_DIR'
PATCHES_DIR='$PATCHES_DIR'
KBUILD_OUT='$KBUILD_OUT'
OUT_DIR='$OUT_DIR'

git -C / config --global --add safe.directory '$DIR'
git -C / config --global --add safe.directory '$KERNEL_DIR'

$(declare -f clean_kernel_tree)
$(declare -f apply_patches)
$(declare -f build_kernel)

build_kernel
EOF
