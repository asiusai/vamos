#!/usr/bin/env bash
set -e
set -o pipefail

VOID_ROOTFS_URL="https://repo-default.voidlinux.org/live/current/void-aarch64-ROOTFS-20250202.tar.xz"
VOID_ROOTFS_SHA256="01a30f17ae06d4d5b322cd579ca971bc479e02cc284ec1e5a4255bea6bac3ce6"

# Make sure we're in the correct spot
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
cd "$DIR"
KVER=$(cat "$DIR/build/kernel-out/include/config/kernel.release")
KMOD_SRC="$DIR/build/modules_install/lib/modules/$KVER"
for module in aic_load_fw/aic_load_fw aic8800_fdrv/aic8800_fdrv; do
  if [ ! -f "$KMOD_SRC/extra/$module.ko" ]; then
    echo "Missing matching AIC8800 modules for $KVER; run ./vamos build kernel first" >&2
    exit 1
  fi
done
. "$DIR/tools/build/openpilot_checkout.sh"

update_openpilot_checkout
OPENPILOT_PYTHON_VERSION="$(tr -d '[:space:]' < "$OP_SRC/.python-version")"
if ! [[ "$OPENPILOT_PYTHON_VERSION" =~ ^3[.]12[.][0-9]+$ ]]; then
  echo "ERROR: unsupported openpilot Python version: $OPENPILOT_PYTHON_VERSION" >&2
  exit 1
fi
"$DIR/tools/build/build_bootloader.sh"

DOWNLOADS_DIR="$DIR/build/downloads"
VOID_ROOTFS_FILE="$DOWNLOADS_DIR/void-aarch64-ROOTFS-20250202.tar.xz"
BUILD_DIR="$DIR/build/tmp-system"
OUTPUT_DIR="$DIR/build"

ROOTFS_DIR="$BUILD_DIR/void-rootfs"
ROOTFS_IMAGE="$BUILD_DIR/system.img"
OUT_IMAGE="$OUTPUT_DIR/system.img"

# Writable staging filesystem only. The shipped image is compressed EROFS.
ROOTFS_IMAGE_SIZE=10G

# Create temp dir if non-existent
mkdir -p "$BUILD_DIR" "$OUTPUT_DIR" "$DOWNLOADS_DIR"

# Download Void rootfs if not done already
if [ ! -f "$VOID_ROOTFS_FILE" ]; then
  echo "Downloading Void Linux rootfs: $VOID_ROOTFS_FILE"
  if ! curl -C - -o "$VOID_ROOTFS_FILE" "$VOID_ROOTFS_URL" --silent --remote-time --fail; then
    echo "Download failed"
    exit 1
  fi
fi

# Check SHA256 sum (shasum is macOS/Ubuntu, sha256sum is Fedora/RHEL)
if command -v sha256sum >/dev/null 2>&1; then
  ACTUAL_HASH=$(sha256sum "$VOID_ROOTFS_FILE" | awk '{print $1}')
else
  ACTUAL_HASH=$(shasum -a 256 "$VOID_ROOTFS_FILE" | awk '{print $1}')
fi
if [ "$ACTUAL_HASH" != "$VOID_ROOTFS_SHA256" ]; then
  echo "Checksum mismatch: got $ACTUAL_HASH, expected $VOID_ROOTFS_SHA256"
  exit 1
fi

# Setup qemu multiarch
if [ "$(uname -m)" = "x86_64" ]; then
  echo "Registering emulator"
  docker run --rm --privileged tonistiigi/binfmt --install all
fi

# Check Dockerfile
export DOCKER_BUILDKIT=1
docker buildx build -f tools/build/Dockerfile --check \
  --build-arg OPENPILOT_PYTHON_VERSION="$OPENPILOT_PYTHON_VERSION" \
  "$DIR"

# Setup mount container for macOS and CI support
echo "Building vamos-builder docker image"
docker build -f tools/build/Dockerfile.builder -t vamos-builder "$DIR" \
  --build-arg UNAME="$(id -nu)" \
  --build-arg UID="$(id -u)" \
  --build-arg GID="$(id -g)"

echo "Starting builder container"
# Include Git metadata for both submodule and detached worktree checkouts.
MOUNT_ROOT="$(git -C "$DIR" rev-parse --show-superproject-working-tree 2>/dev/null || true)"
[ -z "$MOUNT_ROOT" ] && MOUNT_ROOT="$DIR"
GIT_COMMON_DIR="$(git -C "$DIR" rev-parse --path-format=absolute --git-common-dir)"
MOUNT_CONTAINER_ID=$(docker run -d --ulimit nofile=65536:65536 --privileged \
  -v /dev:/dev -v "$MOUNT_ROOT:$MOUNT_ROOT:z" \
  -v "$GIT_COMMON_DIR:$GIT_COMMON_DIR" vamos-builder)

# Cleanup containers on possible exit
trap "echo \"Cleaning up containers:\"; \
docker container rm -f $MOUNT_CONTAINER_ID" EXIT

# Define functions for docker execution
exec_as_user() {
  docker exec -u "$(id -nu)" "$MOUNT_CONTAINER_ID" "$@"
}

exec_as_root() {
  docker exec "$MOUNT_CONTAINER_ID" "$@"
}

# Create filesystem ext4 image
echo "Creating empty filesystem"
exec_as_user fallocate -l "$ROOTFS_IMAGE_SIZE" "$ROOTFS_IMAGE"
exec_as_user mkfs.ext4 "$ROOTFS_IMAGE" &> /dev/null

# Mount filesystem
echo "Mounting empty filesystem"
exec_as_root mkdir -p "$ROOTFS_DIR"
exec_as_root mount "$ROOTFS_IMAGE" "$ROOTFS_DIR"

# Also unmount filesystem (overwrite previous trap)
trap "exec_as_root umount -l $ROOTFS_DIR &> /dev/null || true; \
echo \"Cleaning up containers:\"; \
docker container rm -f $MOUNT_CONTAINER_ID" EXIT

echo "Kernel version from build: $KVER"

echo "Building and extracting vamos docker image"
docker buildx build -f tools/build/Dockerfile --platform=linux/arm64 \
  --output "type=tar,dest=-" \
  --provenance=false \
  --build-arg VOID_ROOTFS="${VOID_ROOTFS_FILE#"$DIR/"}" \
  --build-arg OPENPILOT_PYTHON_VERSION="$OPENPILOT_PYTHON_VERSION" \
  --build-arg KVER="${KVER}" \
  "$DIR" | docker exec -i "$MOUNT_CONTAINER_ID" tar -xf - -C "$ROOTFS_DIR"
echo "Build and extraction complete"

# Avoid detecting as container
echo "Removing .dockerenv file"
exec_as_root rm -f "$ROOTFS_DIR/.dockerenv"

echo "Setting network stuff"
GIT_HASH=${GIT_HASH:-$(git -C "$DIR" rev-parse HEAD)}
DATETIME=$(date '+%Y-%m-%dT%H:%M:%S')
BUILD_EPOCH=$(date +%s)
exec_as_root sh -c "
  set -e
  cd '$ROOTFS_DIR'

  # Trusted local seed for recovering missing or corrupt RTC dates at boot.
  echo '$BUILD_EPOCH' > etc/vamos-build-time

  # Add hostname and hosts
  HOST=asius-v0
  ln -sf /proc/sys/kernel/hostname etc/hostname
  echo '127.0.0.1    localhost.localdomain localhost' > etc/hosts
  echo \"127.0.0.1    \$HOST\" >> etc/hosts

  # DNS: resolv.conf must be writable for NetworkManager
  # Docker mounts resolv.conf during build so we do this after export
  rm -f etc/resolv.conf && ln -s /run/resolv.conf etc/resolv.conf

  # Void's iputils doesn't set CAP_NET_RAW on ping, so non-root gets 'Operation not permitted'
  setcap cap_net_raw+ep bin/iputils-ping

  # Write build info
  printf '%s\n%s\n' '$GIT_HASH' '$DATETIME' > BUILD
"

# Install the in-tree and AIC8800 modules built together for this kernel.
echo "Installing kernel modules for $KVER"
exec_as_root mkdir -p "$ROOTFS_DIR/lib/modules"
exec_as_root cp -a "$KMOD_SRC" "$ROOTFS_DIR/lib/modules/$KVER"
exec_as_root depmod -b "$ROOTFS_DIR" -a "$KVER"

# Use the same managed checkout packaged by build_disk to refresh the system
# Python environment.
# /data is a separate userdata partition at runtime, so files baked below that
# mount point would be hidden and only waste image space.
echo "Staging tracked openpilot sources from $OP_SRC"
if [ -d "$OP_SRC" ]; then
  exec_as_root mkdir -p "$ROOTFS_DIR/data/openpilot"
  GIT_LFS_SKIP_SMUDGE=1 git -C "$OP_SRC" archive HEAD |
    docker exec -i "$MOUNT_CONTAINER_ID" tar -xf - -C "$ROOTFS_DIR/data/openpilot"
  while read -r sub_path; do
    sub_src="$OP_SRC/$sub_path"
    [ -d "$sub_src" ] || {
      echo "Missing openpilot submodule checkout: $sub_path"
      exit 1
    }
    exec_as_root mkdir -p "$ROOTFS_DIR/data/openpilot/$sub_path"
    GIT_LFS_SKIP_SMUDGE=1 git -C "$sub_src" archive HEAD |
      docker exec -i "$MOUNT_CONTAINER_ID" tar -xf - -C "$ROOTFS_DIR/data/openpilot/$sub_path"
  done < <(git -C "$OP_SRC" config --file .gitmodules --get-regexp path | awk '{print $2}')

  echo "Installing openpilot Python dependencies"
  exec_as_root mkdir -p "$ROOTFS_DIR/dev" "$ROOTFS_DIR/proc" "$ROOTFS_DIR/sys" "$ROOTFS_DIR/run"
  exec_as_root cp /etc/resolv.conf "$ROOTFS_DIR/run/resolv.conf"
  exec_as_root mount --bind /dev "$ROOTFS_DIR/dev"
  exec_as_root mount -t proc proc "$ROOTFS_DIR/proc"
  exec_as_root mount --bind /sys "$ROOTFS_DIR/sys"
  set +e
  exec_as_root chroot "$ROOTFS_DIR" sh -c '
    set -e
    mkdir -p /data/tmp /data/uv-cache
    chmod 1777 /data/tmp
    cd /data/openpilot
    XDG_DATA_HOME=/usr/local \
    TMPDIR=/data/tmp \
    UV_CACHE_DIR=/data/uv-cache \
    UV_PROJECT_ENVIRONMENT=/usr/local/venv \
      uv sync --frozen --inexact --no-install-project
    /usr/local/venv/bin/python -c "import av, dbus_fast, libdatachannel, paramiko, PIL, smbus2, usb"
    rm -rf /data/tmp /data/uv-cache
    chmod -R a+rX /usr/local/venv /usr/local/uv
  '
  UV_STATUS=$?
  set -e
  exec_as_root umount "$ROOTFS_DIR/sys"
  exec_as_root umount "$ROOTFS_DIR/proc"
  exec_as_root umount "$ROOTFS_DIR/dev"
  exec_as_root rm -rf "$ROOTFS_DIR/data/openpilot"
  [ "$UV_STATUS" -eq 0 ] || exit "$UV_STATUS"

  # openpilot's packaged FFmpeg development tree includes an OpenCL-only
  # header. V1 has no OpenCL runtime or backend, so do not reintroduce that
  # otherwise inert file after the Docker-stage cleanup.
  exec_as_root find "$ROOTFS_DIR/usr/local/venv" -type f \
    -path '*/site-packages/ffmpeg/install/include/libavutil/hwcontext_opencl.h' \
    -delete
else
  echo "ERROR: managed openpilot checkout is missing: $OP_SRC" >&2
  exit 1
fi

# Run after both dependency installations so the final uv sync cannot restore
# test suites, debug information or the redundant software-only Mesa runtime.
echo "Trimming runtime dependencies"
exec_as_root python3 "$DIR/userspace/trim.py" "$ROOTFS_DIR" --strip /usr/lib/llvm21/bin/llvm-strip
echo "Deduplicating immutable Python environment files"
exec_as_root hardlink -X -s 4096 "$ROOTFS_DIR/usr/local/venv"

# Profile rootfs (before unmount)
echo "Profiling rootfs"
MOUNT_CONTAINER_ID="$MOUNT_CONTAINER_ID" ROOTFS_DIR="$ROOTFS_DIR" \
  ROOTFS_IMAGE="$ROOTFS_IMAGE" OUTPUT_DIR="$OUTPUT_DIR" \
  "$DIR/vamos" profile

EROFS_IMAGE="$BUILD_DIR/system.erofs.img"
# Keep ownership and capabilities. Deduplication shares repeated data blocks.
EROFS_COMPRESSION="${VAMOS_EROFS_COMPRESSION:-zstd,9}"
echo "Building EROFS image ($EROFS_COMPRESSION, 64K clusters, deduplication)"
exec_as_root mkfs.erofs \
  "-z$EROFS_COMPRESSION" -C65536 -Ededupe -T0 --mkfs-time --quiet \
  "$EROFS_IMAGE" "$ROOTFS_DIR"
exec_as_root fsck.erofs --extract "$EROFS_IMAGE"
exec_as_root dump.erofs --cat --path=/VERSION "$EROFS_IMAGE"
exec_as_user cp "$EROFS_IMAGE" "$OUT_IMAGE"

# Unmount image
echo "Unmount filesystem"
exec_as_root sync
exec_as_root umount "$ROOTFS_DIR"

# Record the actual programmed image size, excluding unused partition space.
IMAGE_SIZE=$(stat -c%s "$OUT_IMAGE" 2>/dev/null || stat -f%z "$OUT_IMAGE")
if command -v jq &>/dev/null; then
  jq --arg s "$IMAGE_SIZE" '.image_size_sparse_bytes = ($s | tonumber) | .image_format = "erofs"' \
    "$OUTPUT_DIR/rootfs-profile.json" > "$OUTPUT_DIR/rootfs-profile.json.tmp" && \
    mv "$OUTPUT_DIR/rootfs-profile.json.tmp" "$OUTPUT_DIR/rootfs-profile.json"
fi

# Size comparison
echo ""
echo "=== Image size ==="
echo "EROFS: $(numfmt --to=iec-i --suffix=B "$IMAGE_SIZE") ($IMAGE_SIZE bytes)"
echo ""

echo "Done!"
