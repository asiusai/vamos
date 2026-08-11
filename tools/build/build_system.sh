#!/usr/bin/env bash
set -e
set -o pipefail

VOID_ROOTFS_URL="https://repo-default.voidlinux.org/live/current/void-aarch64-ROOTFS-20250202.tar.xz"
VOID_ROOTFS_SHA256="01a30f17ae06d4d5b322cd579ca971bc479e02cc284ec1e5a4255bea6bac3ce6"

# Make sure we're in the correct spot
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
cd "$DIR"
. "$DIR/tools/build/openpilot_checkout.sh"

update_openpilot_checkout
"$DIR/tools/build/build_bootloader.sh"

DOWNLOADS_DIR="$DIR/build/downloads"
VOID_ROOTFS_FILE="$DOWNLOADS_DIR/void-aarch64-ROOTFS-20250202.tar.xz"
BUILD_DIR="$DIR/build/tmp-system"
OUTPUT_DIR="$DIR/build"

ROOTFS_DIR="$BUILD_DIR/void-rootfs"
ROOTFS_IMAGE="$BUILD_DIR/system.img"
OUT_IMAGE="$OUTPUT_DIR/system.img"

# the partition is 10G, but openpilot's updater didn't always handle the full size
# Increased from 4500M to 6G for Python packages
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
docker buildx build -f tools/build/Dockerfile --check "$DIR"

# Setup mount container for macOS and CI support
echo "Building vamos-builder docker image"
docker build -f tools/build/Dockerfile.builder -t vamos-builder "$DIR" \
  --build-arg UNAME="$(id -nu)" \
  --build-arg UID="$(id -u)" \
  --build-arg GID="$(id -g)"

echo "Starting builder container"
# If vamOS is itself a git submodule, mount the outer superproject so that
# nested .git gitfiles resolve inside the container.
MOUNT_ROOT="$(git -C "$DIR" rev-parse --show-superproject-working-tree 2>/dev/null || true)"
[ -z "$MOUNT_ROOT" ] && MOUNT_ROOT="$DIR"
MOUNT_CONTAINER_ID=$(docker run -d --ulimit nofile=65536:65536 --privileged -v /dev:/dev -v "$MOUNT_ROOT:$MOUNT_ROOT:z" vamos-builder)

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

KVER=""
if [ -f "$DIR/build/kernel-out/include/config/kernel.release" ]; then
  KVER=$(cat "$DIR/build/kernel-out/include/config/kernel.release")
  echo "Kernel version from build: $KVER"
fi

echo "Building and extracting vamos docker image"
docker buildx build -f tools/build/Dockerfile --platform=linux/arm64 \
  --output "type=tar,dest=-" \
  --provenance=false \
  --build-arg VOID_ROOTFS="${VOID_ROOTFS_FILE#"$DIR/"}" \
  --build-arg KVER="${KVER}" \
  "$DIR" | docker exec -i "$MOUNT_CONTAINER_ID" tar -xf - -C "$ROOTFS_DIR"
echo "Build and extraction complete"

# Avoid detecting as container
echo "Removing .dockerenv file"
exec_as_root rm -f "$ROOTFS_DIR/.dockerenv"

echo "Setting network stuff"
GIT_HASH=${GIT_HASH:-$(git --git-dir="$DIR/.git" rev-parse HEAD)}
DATETIME=$(date '+%Y-%m-%dT%H:%M:%S')
exec_as_root sh -c "
  set -e
  cd '$ROOTFS_DIR'

  # Add hostname and hosts
  HOST=asius-v1
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

# Install kernel modules (in-tree + out-of-tree prebuilt)
if [ -n "$KVER" ]; then
  echo "Installing kernel modules for $KVER"
  KMOD_SRC="$DIR/build/modules_install/lib/modules/$KVER"
  exec_as_root mkdir -p "$ROOTFS_DIR/lib/modules"
  if [ -d "$KMOD_SRC" ]; then
    exec_as_root cp -a "$KMOD_SRC" "$ROOTFS_DIR/lib/modules/$KVER"
  else
    exec_as_root mkdir -p "$ROOTFS_DIR/lib/modules/$KVER"
    exec_as_root sh -c "touch '$ROOTFS_DIR/lib/modules/$KVER/modules.order' '$ROOTFS_DIR/lib/modules/$KVER/modules.builtin' '$ROOTFS_DIR/lib/modules/$KVER/modules.builtin.modinfo'"
  fi
  exec_as_root mkdir -p "$ROOTFS_DIR/lib/modules/$KVER/extra"
  for ko in aic_load_fw.ko aic8800_fdrv.ko aic_btusb.ko; do
    if [ -f "$DIR/kernel/modules/$ko" ]; then
      exec_as_root cp "$DIR/kernel/modules/$ko" "$ROOTFS_DIR/lib/modules/$KVER/extra/"
    fi
  done
  exec_as_root depmod -b "$ROOTFS_DIR" -a "$KVER" 2>/dev/null || true
fi

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

  echo "Deduplicating immutable Python environment files"
  exec_as_root hardlink -X -s 4096 "$ROOTFS_DIR/usr/local/venv"
else
  echo "ERROR: managed openpilot checkout is missing: $OP_SRC" >&2
  exit 1
fi

# Profile rootfs (before unmount)
echo "Profiling rootfs"
MOUNT_CONTAINER_ID="$MOUNT_CONTAINER_ID" ROOTFS_DIR="$ROOTFS_DIR" \
  ROOTFS_IMAGE="$ROOTFS_IMAGE" OUTPUT_DIR="$OUTPUT_DIR" \
  "$DIR/vamos" profile

EROFS_IMAGE="$BUILD_DIR/system.erofs.img"
OUT_EROFS_IMAGE="$OUTPUT_DIR/system.erofs.img"
if [ "${VAMOS_SKIP_EROFS:-0}" != "1" ]; then
  # Build EROFS before unmount while the rootfs is still mounted.
  echo "Building EROFS image (LZ4HC, 64K clusters)"
  exec_as_root mkfs.erofs \
    -zlz4hc,12 \
    -C65536 \
    -T0 \
    --all-root \
    --quiet \
    -x-1 \
    "$EROFS_IMAGE" "$ROOTFS_DIR"
fi

# Unmount image
echo "Unmount filesystem"
exec_as_root sync
exec_as_root umount "$ROOTFS_DIR"

# Deleted and deduplicated data otherwise remains in free ext4 blocks and
# bloats the compressed OTA payload.
echo "Checking filesystem before zeroing"
exec_as_root e2fsck -fp "$ROOTFS_IMAGE"
echo "Zeroing free filesystem blocks"
exec_as_root zerofree "$ROOTFS_IMAGE"
echo "Checking filesystem after zeroing"
exec_as_root e2fsck -fn "$ROOTFS_IMAGE"

# Copy raw ext4 image to output. edl-ng write-sector takes raw bytes, not the
# Android-sparse format qdl used to consume — so no img2simg step.
echo "Copying system image to output"
exec_as_user cp "$ROOTFS_IMAGE" "$OUT_IMAGE"

if [ "${VAMOS_SKIP_EROFS:-0}" != "1" ]; then
  cp "$EROFS_IMAGE" "$OUT_EROFS_IMAGE"
fi

# Patch sparse image size into profile JSON
SPARSE_SIZE=$(stat -c%s "$OUT_IMAGE" 2>/dev/null || stat -f%z "$OUT_IMAGE")
if command -v jq &>/dev/null; then
  jq --arg s "$SPARSE_SIZE" '.image_size_sparse_bytes = ($s | tonumber)' \
    "$OUTPUT_DIR/rootfs-profile.json" > "$OUTPUT_DIR/rootfs-profile.json.tmp" && \
    mv "$OUTPUT_DIR/rootfs-profile.json.tmp" "$OUTPUT_DIR/rootfs-profile.json"
fi

# Size comparison
EXT4_SPARSE_SIZE=$(stat -c%s "$OUT_IMAGE" 2>/dev/null || stat -f%z "$OUT_IMAGE")
echo ""
echo "=== Image size ==="
echo "ext4 (sparse): $(numfmt --to=iec-i --suffix=B "$EXT4_SPARSE_SIZE") ($EXT4_SPARSE_SIZE bytes)"
if [ "${VAMOS_SKIP_EROFS:-0}" != "1" ]; then
  EROFS_SIZE=$(stat -c%s "$OUT_EROFS_IMAGE" 2>/dev/null || stat -f%z "$OUT_EROFS_IMAGE")
  echo "EROFS (LZ4HC): $(numfmt --to=iec-i --suffix=B "$EROFS_SIZE") ($EROFS_SIZE bytes)"
fi
echo ""

echo "Done!"
