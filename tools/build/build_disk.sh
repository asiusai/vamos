#!/usr/bin/env bash
# Assemble a compact initial disk image for Radxa Dragon Q6A.
# `vamos-update initialize` migrates it to A/B plus persistent userdata.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
cd "$DIR"

BUILD_DIR="$DIR/build"
DISK_IMG="$BUILD_DIR/dragon.img"

KERNEL_IMAGE="$BUILD_DIR/Image"
DTB_FILE="$BUILD_DIR/qcs6490-radxa-dragon-q6a.dtb"
ROOTFS_IMG="$BUILD_DIR/system.img"
INITIAL_DIR="$BUILD_DIR/tmp-disk"
INITIAL_ROOTFS_IMG="$INITIAL_DIR/system.initial.img"
. "$DIR/tools/build/openpilot_checkout.sh"

if [ ! -f "$KERNEL_IMAGE" ]; then
  echo "ERROR: kernel Image not found at $KERNEL_IMAGE"
  echo "Run: ./vamos build kernel"
  exit 1
fi
if [ ! -f "$DTB_FILE" ]; then
  echo "ERROR: DTB not found at $DTB_FILE"
  echo "Run: ./vamos build kernel"
  exit 1
fi
if [ ! -f "$ROOTFS_IMG" ]; then
  echo "ERROR: rootfs not found at $ROOTFS_IMG"
  echo "Run: ./vamos build system"
  exit 1
fi

update_openpilot_checkout

OP_COMMIT="$(git -C "$OP_SRC" rev-parse HEAD)"
OP_DATE="$(git -C "$OP_SRC" log -1 --format=%ci)"
OP_VERSION="$(sed -n 's/.*COMMA_VERSION[[:space:]]*"\([^"]*\)".*/\1/p' "$OP_SRC/openpilot/common/version.h" | head -1)"

mkdir -p "$INITIAL_DIR"
echo "== Preparing initial rootfs with openpilot $OP_COMMIT =="
cp --reflink=auto --sparse=always "$ROOTFS_IMG" "$INITIAL_ROOTFS_IMG"

if ! docker image inspect vamos-builder >/dev/null 2>&1; then
  docker build -f "$DIR/tools/build/Dockerfile.builder" -t vamos-builder "$DIR" \
    --build-arg UNAME="$(id -nu)" \
    --build-arg UID="$(id -u)" \
    --build-arg GID="$(id -g)"
fi

MOUNT_ROOT="$(git -C "$DIR" rev-parse --show-superproject-working-tree 2>/dev/null || true)"
[ -n "$MOUNT_ROOT" ] || MOUNT_ROOT="$(dirname "$DIR")"
MOUNT_DIR="$INITIAL_DIR/rootfs"
MOUNT_CONTAINER_ID="$(docker run -d --privileged -v /dev:/dev -v "$MOUNT_ROOT:$MOUNT_ROOT:z" vamos-builder)"
mounted=0
cleanup_mount() {
  if [ "$mounted" -eq 1 ]; then
    docker exec "$MOUNT_CONTAINER_ID" umount "$MOUNT_DIR" >/dev/null 2>&1 || true
  fi
  docker container rm -f "$MOUNT_CONTAINER_ID" >/dev/null 2>&1 || true
}
trap cleanup_mount EXIT

docker exec "$MOUNT_CONTAINER_ID" mkdir -p "$MOUNT_DIR"
docker exec "$MOUNT_CONTAINER_ID" mount -o loop "$INITIAL_ROOTFS_IMG" "$MOUNT_DIR"
mounted=1

OP_DST="$MOUNT_DIR/data/openpilot"
CONTINUE="$MOUNT_DIR/data/continue.sh"
docker exec "$MOUNT_CONTAINER_ID" rm -rf "$OP_DST"
docker exec "$MOUNT_CONTAINER_ID" mkdir -p "$OP_DST"

# The managed checkout is standalone, so copying it preserves working Git and
# submodule metadata without rewriting paths on the target.
tar -C "$OP_SRC" -cf - . \
  | docker exec -i "$MOUNT_CONTAINER_ID" tar -C "$OP_DST" -xf -

docker exec "$MOUNT_CONTAINER_ID" bash -c '
set -euo pipefail

OP_DST="$1"
CONTINUE="$2"
OP_COMMIT="$3"
OP_DATE="$4"
OP_REPO="$5"
OP_VERSION="$6"

git config --global --add safe.directory "$OP_DST"

linked=0
linked_bytes=0
while read -r oid marker path; do
  worktree="$OP_DST/$path"
  object="$OP_DST/.git/lfs/objects/${oid:0:2}/${oid:2:2}/$oid"
  [ -f "$object" ] && [ -f "$worktree" ] || continue
  cmp -s "$object" "$worktree" || continue
  mode="$(stat -c %a "$worktree")"
  ln -f "$object" "$worktree"
  chmod "$mode" "$worktree"
  linked=$((linked + 1))
  linked_bytes=$((linked_bytes + $(stat -c %s "$worktree")))
done < <(git -C "$OP_DST" lfs ls-files -l)

cat > "$OP_DST/build.json" <<BUILDJSON
{
  "channel": "master",
  "openpilot": {
    "version": "$OP_VERSION",
    "release_notes": "",
    "git_commit": "$OP_COMMIT",
    "git_origin": "$OP_REPO",
    "git_commit_date": "$OP_DATE",
    "build_style": "source"
  }
}
BUILDJSON

cat >> "$OP_DST/.git/info/exclude" <<GITEXCLUDE
/build.json
/build/
/scons_cache/
/openpilot/selfdrive/modeld/models/*_tinygrad.pkl
/openpilot/selfdrive/modeld/models/*_tinygrad.pkl.chunk*
/openpilot/selfdrive/modeld/models/*_metadata.pkl
/openpilot/selfdrive/modeld/models/*.prebuilt.pkl
/openpilot/selfdrive/modeld/models/tg_compiled_flags.json
GITEXCLUDE

git -C "$OP_DST" lfs fsck
if [ -n "$(git -C "$OP_DST" status --short --untracked-files=no)" ]; then
  git -C "$OP_DST" status --short --untracked-files=no >&2
  echo "ERROR: packaged openpilot checkout is dirty" >&2
  exit 1
fi
if git -C "$OP_DST" submodule status --recursive | grep -q "^[+-U]"; then
  git -C "$OP_DST" submodule status --recursive >&2
  echo "ERROR: packaged openpilot submodules do not match their gitlinks" >&2
  exit 1
fi

cat > "$CONTINUE.tmp" <<CONT
#!/usr/bin/env bash
cd /data/openpilot
exec /data/openpilot/launch_openpilot.sh
CONT
chmod 755 "$CONTINUE.tmp"
mv "$CONTINUE.tmp" "$CONTINUE"
chown -R 1000:1000 "$OP_DST" "$CONTINUE"

echo "Packaged openpilot $OP_COMMIT; hardlinked $linked LFS files ($linked_bytes bytes)"
df -h "$(dirname "$OP_DST")"
' _ "$OP_DST" "$CONTINUE" "$OP_COMMIT" "$OP_DATE" "$OP_REPO" "$OP_VERSION"

docker exec "$MOUNT_CONTAINER_ID" sync
docker exec "$MOUNT_CONTAINER_ID" umount "$MOUNT_DIR"
mounted=0
docker container rm -f "$MOUNT_CONTAINER_ID" >/dev/null
trap - EXIT

e2fsck -fn "$INITIAL_ROOTFS_IMG"

ESP_SIZE_MB=256
ESP_IMG="$BUILD_DIR/esp.img"
INITIAL_ESP_IMG="$INITIAL_DIR/esp_a.img"
INITIAL_GRUBENV="$INITIAL_DIR/grubenv"

ROOTFS_BYTES=$(stat -c%s "$INITIAL_ROOTFS_IMG")
ROOTFS_SECTORS=$(( (ROOTFS_BYTES + 511) / 512 ))

ESP_SECTORS=$(( ESP_SIZE_MB * 1024 * 1024 / 512 ))
START_SECTOR=2048                    # 1MiB offset for MBR/GPT + alignment
ESP_START=$START_SECTOR
ESP_END=$(( ESP_START + ESP_SECTORS - 1 ))
ROOTFS_START=$(( ESP_END + 1 ))
ROOTFS_END=$(( ROOTFS_START + ROOTFS_SECTORS - 1 ))
# +2048 for secondary GPT
TOTAL_SECTORS=$(( ROOTFS_END + 2048 ))
TOTAL_BYTES=$(( TOTAL_SECTORS * 512 ))

echo "== vamOS disk layout =="
echo "  ESP:    sectors $ESP_START..$ESP_END  (${ESP_SIZE_MB} MiB)"
echo "  rootfs: sectors $ROOTFS_START..$ROOTFS_END  ($(numfmt --to=iec-i --suffix=B "$ROOTFS_BYTES"))"
echo "  total:  sectors 0..$TOTAL_SECTORS      ($(numfmt --to=iec-i --suffix=B "$TOTAL_BYTES"))"

# ---- Build ESP (FAT32) ----
"$DIR/tools/build/build_esp.sh"
cp --reflink=auto --sparse=always "$ESP_IMG" "$INITIAL_ESP_IMG"
mlabel -i "$INITIAL_ESP_IMG" ::VAMOS-A

# ---- Assemble full disk image ----
echo "== Assembling $DISK_IMG =="
rm -f "$DISK_IMG"
truncate -s "$TOTAL_BYTES" "$DISK_IMG"

# GPT + partitions
sgdisk --clear \
       --new=1:${ESP_START}:${ESP_END} \
       --typecode=1:ef00 --change-name=1:esp_a \
       --new=2:${ROOTFS_START}:${ROOTFS_END} \
       --typecode=2:8300 --change-name=2:rootfs_a \
       "$DISK_IMG" >/dev/null

ROOTFS_PARTUUID="$(sgdisk --info=2 "$DISK_IMG" | sed -n 's/^Partition unique GUID: //p')"
if [ -z "$ROOTFS_PARTUUID" ]; then
  echo "ERROR: failed to read initial rootfs PARTUUID" >&2
  exit 1
fi
grub-editenv "$INITIAL_GRUBENV" create
grub-editenv "$INITIAL_GRUBENV" set \
  generation=1 active=a pending= phase=stable \
  root_a="PARTUUID=$ROOTFS_PARTUUID" root_b=PARTLABEL=rootfs_b
mcopy -o -i "$INITIAL_ESP_IMG" "$INITIAL_GRUBENV" ::/EFI/vamos/grubenv

# Copy partition contents into the disk image at the right offsets
dd if="$INITIAL_ESP_IMG" of="$DISK_IMG" bs=512 seek=$ESP_START conv=notrunc status=none
dd if="$INITIAL_ROOTFS_IMG" of="$DISK_IMG" bs=512 seek=$ROOTFS_START conv=notrunc,sparse status=none

echo "== Done =="
ls -lh "$DISK_IMG"
echo ""
sgdisk --print "$DISK_IMG" 2>/dev/null | tail -n +6
