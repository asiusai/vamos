#!/usr/bin/env bash
set -e
set -o pipefail

VOID_ROOTFS_URL="https://repo-default.voidlinux.org/live/current/void-aarch64-ROOTFS-20250202.tar.xz"
VOID_ROOTFS_SHA256="01a30f17ae06d4d5b322cd579ca971bc479e02cc284ec1e5a4255bea6bac3ce6"

# Make sure we're in the correct spot
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
cd "$DIR"

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
  HOST=asius-one
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

# Copy openpilot into /data/openpilot so first boot works offline.
# Path resolution: vamos may be a submodule; the outer asius repo contains
# openpilot/ alongside vamos/. Fall back to not baking if not found.
OP_SRC=""
for cand in "$DIR/../openpilot" "$(git -C "$DIR" rev-parse --show-superproject-working-tree 2>/dev/null)/openpilot"; do
  [ -d "$cand" ] && OP_SRC="$(cd "$cand" && pwd)" && break
done
if [ -n "$OP_SRC" ]; then
  echo "Copying openpilot from $OP_SRC into /data/openpilot"

  # Generate build.json from git metadata. No openpilot build runs in this script.
  OP_COMMIT=$(git -C "$OP_SRC" rev-parse HEAD 2>/dev/null || echo "unknown")
  OP_BRANCH=$(git -C "$OP_SRC" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
  OP_ORIGIN=$(git -C "$OP_SRC" remote get-url origin 2>/dev/null || echo "unknown")
  OP_PULL_ORIGIN="$OP_ORIGIN"
  case "$OP_PULL_ORIGIN" in
    git@github.com:*) OP_PULL_ORIGIN="https://github.com/${OP_PULL_ORIGIN#git@github.com:}" ;;
  esac
  OP_DATE=$(git -C "$OP_SRC" log -1 --format=%ci 2>/dev/null || echo "unknown")
  OP_VERSION=$(cat "$OP_SRC/openpilot/common/version.h" 2>/dev/null | grep COMMA_VERSION | head -1 | sed 's/.*"\(.*\)".*/\1/' || echo "0.0.0")
  OP_CLONE_BRANCH="$OP_BRANCH"
  if [ "$OP_CLONE_BRANCH" = "HEAD" ] || [ "$OP_CLONE_BRANCH" = "unknown" ]; then
    OP_CLONE_BRANCH="one"
  fi

  # All fs ops run inside the builder container — ROOTFS_DIR only exists there
  exec_as_root rm -rf "$ROOTFS_DIR/data/openpilot"
  exec_as_root mkdir -p "$ROOTFS_DIR/data"
  exec_as_root git \
    -c safe.directory="$OP_SRC" \
    -c protocol.file.allow=always \
    clone --depth 1 --single-branch --branch "$OP_CLONE_BRANCH" \
    "file://$OP_SRC" "$ROOTFS_DIR/data/openpilot"
  exec_as_root git -C "$ROOTFS_DIR/data/openpilot" remote set-url origin "$OP_PULL_ORIGIN"
  exec_as_root git -C "$ROOTFS_DIR/data/openpilot" config branch.one.remote origin
  exec_as_root git -C "$ROOTFS_DIR/data/openpilot" config branch.one.merge refs/heads/one
  exec_as_root git -C "$ROOTFS_DIR/data/openpilot" submodule init

  for spec in \
    msgq_repo:msgq \
    opendbc_repo:opendbc \
    panda:panda \
    rednose_repo:rednose_repo \
    teleoprtc_repo:teleoprtc_repo \
    tinygrad_repo:tinygrad
  do
    sub_path=${spec%%:*}
    sub_name=${spec#*:}
    sub_src="$OP_SRC/$sub_path"
    sub_git_dir=$(git -C "$sub_src" rev-parse --absolute-git-dir 2>/dev/null || true)
    [ -d "$sub_src" ] || continue

    exec_as_root rm -rf "$ROOTFS_DIR/data/openpilot/$sub_path"
    exec_as_root cp -a "$sub_src" "$ROOTFS_DIR/data/openpilot/$sub_path"
    if [ -n "$sub_git_dir" ] && [ -d "$sub_git_dir" ]; then
      exec_as_root mkdir -p "$ROOTFS_DIR/data/openpilot/.git/modules"
      exec_as_root rm -rf "$ROOTFS_DIR/data/openpilot/.git/modules/$sub_name"
      exec_as_root cp -a "$sub_git_dir" "$ROOTFS_DIR/data/openpilot/.git/modules/$sub_name"
      exec_as_root sh -c "
        printf 'gitdir: ../.git/modules/%s\n' '$sub_name' > '$ROOTFS_DIR/data/openpilot/$sub_path/.git'
        git config --file '$ROOTFS_DIR/data/openpilot/.git/modules/$sub_name/config' core.worktree '../../../$sub_path'
      "
    fi
  done

  exec_as_root sh -c "
    cat > '$ROOTFS_DIR/data/openpilot/build.json' <<BUILDJSON
{
  \"channel\": \"$OP_BRANCH\",
  \"openpilot\": {
    \"version\": \"$OP_VERSION\",
    \"release_notes\": \"\",
    \"git_commit\": \"$OP_COMMIT\",
    \"git_origin\": \"$OP_PULL_ORIGIN\",
    \"git_commit_date\": \"$OP_DATE\",
    \"build_style\": \"source\"
  }
}
BUILDJSON
    cat >> '$ROOTFS_DIR/data/openpilot/.git/info/exclude' <<'GITEXCLUDE'
/build.json
/scons_cache/
/openpilot/selfdrive/modeld/models/*_tinygrad.pkl
/openpilot/selfdrive/modeld/models/*_tinygrad.pkl.chunk*
/openpilot/selfdrive/modeld/models/*_metadata.pkl
/openpilot/selfdrive/modeld/models/*.prebuilt.pkl
/openpilot/selfdrive/modeld/models/tg_compiled_flags.json
GITEXCLUDE
    cd '$ROOTFS_DIR/data/openpilot' && \
    rm -rf build scons_cache && \
    find . -name __pycache__ -type d -prune -exec rm -rf {} + ; \
    find . -name '*.o' -delete
    # Drop generated model artifacts from the copy so first launch builds them
    # on the Dragon instead of using files produced on this host.
    find openpilot/selfdrive/modeld/models -type f \( \
      -name '*_tinygrad.pkl' -o \
      -name '*_tinygrad.pkl.chunk*' -o \
      -name '*_metadata.pkl' -o \
      -name '*.prebuilt.pkl' -o \
      -name 'tg_compiled_flags.json' \
    \) -delete
    cat > '$ROOTFS_DIR/data/continue.sh' <<'CONT'
#!/usr/bin/env bash
cd /data/openpilot
exec /data/openpilot/launch_openpilot.sh
CONT
    chmod +x '$ROOTFS_DIR/data/continue.sh'
    chown -R 1000:1000 '$ROOTFS_DIR/data/openpilot' '$ROOTFS_DIR/data/continue.sh'
  "

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
  exec_as_root umount -l "$ROOTFS_DIR/sys" || true
  exec_as_root umount -l "$ROOTFS_DIR/proc" || true
  exec_as_root umount -l "$ROOTFS_DIR/dev" || true
  [ "$UV_STATUS" -eq 0 ] || exit "$UV_STATUS"

  echo "Deduplicating immutable Python environment files"
  exec_as_root hardlink -X -s 4096 "$ROOTFS_DIR/usr/local/venv"

  echo "Deduplicating identical openpilot LFS worktree files"
  exec_as_root bash -c "
    set -euo pipefail
    repo='$ROOTFS_DIR/data/openpilot'
    linked=0
    linked_bytes=0
    skipped=0

    while read -r oid marker path; do
      object=\"\$repo/.git/lfs/objects/\${oid:0:2}/\${oid:2:2}/\$oid\"
      worktree=\"\$repo/\$path\"

      [ -f \"\$object\" ] && [ -f \"\$worktree\" ]
      cmp -s \"\$object\" \"\$worktree\"

      # Hardlinks cannot retain different metadata. The tici updater is
      # executable only in the worktree, so it intentionally stays separate.
      if [ \"\$(stat -c '%a:%u:%g' \"\$object\")\" != \"\$(stat -c '%a:%u:%g' \"\$worktree\")\" ]; then
        skipped=\$((skipped + 1))
        continue
      fi

      if [ ! \"\$object\" -ef \"\$worktree\" ]; then
        size=\$(stat -c %s \"\$object\")
        ln -f -- \"\$object\" \"\$worktree\"
        linked=\$((linked + 1))
        linked_bytes=\$((linked_bytes + size))
      fi
    done < <(git -c safe.directory=\"\$repo\" -C \"\$repo\" lfs ls-files -l)

    git -c safe.directory=\"\$repo\" -C \"\$repo\" lfs fsck
    [ -z \"\$(git -c safe.directory=\"\$repo\" -C \"\$repo\" status --short --untracked-files=no)\" ]
    printf 'Hardlinked %d LFS files (%d bytes); skipped %d metadata mismatches\n' \
      \"\$linked\" \"\$linked_bytes\" \"\$skipped\"
  "

else
  echo "WARN: openpilot not found next to vamos; /data/openpilot will be empty on first boot"
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
