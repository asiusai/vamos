#!/usr/bin/env bash
# Assemble the final A/B Dragon disk layout plus an optional openpilot userdata
# image. The common factory disk always contains blank userdata; browser and
# manual flashers may apply userdata-openpilot.img as a second, optional step.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
cd "$DIR"

BUILD_DIR="$DIR/build"
DISK_IMG="$BUILD_DIR/dragon.img"
LAYOUT_JSON="$BUILD_DIR/factory-layout.json"

KERNEL_IMAGE="$BUILD_DIR/Image"
DTB_FILE="$BUILD_DIR/qcs6490-radxa-dragon-q6a.dtb"
ROOTFS_IMG="$BUILD_DIR/system.img"
ESP_IMG="$BUILD_DIR/esp.img"
ESP_A_IMG="$BUILD_DIR/esp_a.img"
ESP_B_IMG="$BUILD_DIR/esp_b.img"
ROOTFS_B_IMG="$BUILD_DIR/rootfs_b.img"
USERDATA_IMG="$BUILD_DIR/userdata.img"
USERDATA_OPENPILOT_IMG="$BUILD_DIR/userdata-openpilot.img"
GRUBENV_A="$BUILD_DIR/grubenv_a.factory"
GRUBENV_B="$BUILD_DIR/grubenv_b.factory"

# Build against the smallest shipping v1 storage geometry (the 64 GB eMMC).
# The same image can be written to larger 512-byte-sector storage; flashers
# reject media smaller than the signed image before erasing anything.
SECTOR_SIZE=512
STORAGE_SECTORS="${VAMOS_STORAGE_SECTORS:-122142720}"
ESP_SIZE=$((256 * 1024 * 1024))
SYSTEM_SIZE=$((10 * 1024 * 1024 * 1024))
START_SECTOR=2048

. "$DIR/tools/build/openpilot_checkout.sh"

for input in "$KERNEL_IMAGE" "$DTB_FILE" "$ROOTFS_IMG"; do
  if [ ! -f "$input" ]; then
    echo "ERROR: required build artifact not found: $input" >&2
    echo "Run: ./vamos build kernel && ./vamos build system" >&2
    exit 1
  fi
done
if [ "$(stat -c%s "$ROOTFS_IMG")" -ne "$SYSTEM_SIZE" ]; then
  echo "ERROR: $ROOTFS_IMG must be exactly $SYSTEM_SIZE bytes" >&2
  exit 1
fi
if ! [[ "$STORAGE_SECTORS" =~ ^[0-9]+$ ]] || [ "$STORAGE_SECTORS" -le 0 ]; then
  echo "ERROR: invalid VAMOS_STORAGE_SECTORS: $STORAGE_SECTORS" >&2
  exit 1
fi

update_openpilot_checkout

if ! docker image inspect vamos-builder >/dev/null 2>&1; then
  docker build -f "$DIR/tools/build/Dockerfile.builder" -t vamos-builder "$DIR" \
    --build-arg UNAME="$(id -nu)" \
    --build-arg UID="$(id -u)" \
    --build-arg GID="$(id -g)"
fi

MOUNT_ROOT="$(git -C "$DIR" rev-parse --show-superproject-working-tree 2>/dev/null || true)"
[ -n "$MOUNT_ROOT" ] || MOUNT_ROOT="$(dirname "$DIR")"
MOUNT_DIR="$BUILD_DIR/tmp-disk/mnt"
MOUNT_CONTAINER_ID="$(docker run -d --privileged -v /dev:/dev -v "$MOUNT_ROOT:$MOUNT_ROOT:z" vamos-builder)"
mounted=0
cleanup_mount() {
  if [ "$mounted" -eq 1 ]; then
    docker exec "$MOUNT_CONTAINER_ID" umount "$MOUNT_DIR" >/dev/null 2>&1 || true
  fi
  docker container rm -f "$MOUNT_CONTAINER_ID" >/dev/null 2>&1 || true
}
trap cleanup_mount EXIT

mount_image() {
  local image="$1"
  docker exec "$MOUNT_CONTAINER_ID" mkdir -p "$MOUNT_DIR"
  docker exec "$MOUNT_CONTAINER_ID" mount -o loop "$image" "$MOUNT_DIR"
  mounted=1
}

unmount_image() {
  docker exec "$MOUNT_CONTAINER_ID" sync
  docker exec "$MOUNT_CONTAINER_ID" umount "$MOUNT_DIR"
  mounted=0
}

partition_start() {
  local number="$1"
  sgdisk --info="$number" "$DISK_IMG" | sed -n 's/^First sector: \([0-9][0-9]*\).*/\1/p'
}

partition_sectors() {
  local number="$1"
  sgdisk --info="$number" "$DISK_IMG" | sed -n 's/^Partition size: \([0-9][0-9]*\) sectors.*/\1/p'
}

write_partition() {
  local image="$1"
  local start_sector="$2"
  echo "  $(basename "$image") -> sector $start_sector"
  dd if="$image" of="$DISK_IMG" bs=4M iflag=fullblock oflag=seek_bytes \
    seek="$((start_sector * SECTOR_SIZE))" conv=notrunc,sparse status=none
}

mkdir -p "$BUILD_DIR/tmp-disk"

echo "== Building final factory GPT =="
rm -f "$DISK_IMG"
truncate -s "$((STORAGE_SECTORS * SECTOR_SIZE))" "$DISK_IMG"
ESP_SECTORS=$((ESP_SIZE / SECTOR_SIZE))
SYSTEM_SECTORS=$((SYSTEM_SIZE / SECTOR_SIZE))
ESP_A_START=$START_SECTOR
ROOTFS_A_START=$((ESP_A_START + ESP_SECTORS))
ESP_B_START=$((ROOTFS_A_START + SYSTEM_SECTORS))
ROOTFS_B_START=$((ESP_B_START + ESP_SECTORS))
USERDATA_START=$((ROOTFS_B_START + SYSTEM_SECTORS))

sgdisk --clear \
  --new=1:${ESP_A_START}:$((ROOTFS_A_START - 1)) \
  --typecode=1:ef00 --change-name=1:esp_a \
  --new=2:${ROOTFS_A_START}:$((ESP_B_START - 1)) \
  --typecode=2:8300 --change-name=2:rootfs_a \
  --new=3:${ESP_B_START}:$((ROOTFS_B_START - 1)) \
  --typecode=3:ef00 --change-name=3:esp_b \
  --new=4:${ROOTFS_B_START}:$((USERDATA_START - 1)) \
  --typecode=4:8300 --change-name=4:rootfs_b \
  --new=5:${USERDATA_START}:0 \
  --typecode=5:8300 --change-name=5:userdata \
  "$DISK_IMG" >/dev/null
sgdisk --verify "$DISK_IMG"

USERDATA_SECTORS="$(partition_sectors 5)"
if [ -z "$USERDATA_SECTORS" ]; then
  echo "ERROR: failed to determine userdata size" >&2
  exit 1
fi
USERDATA_SIZE=$((USERDATA_SECTORS * SECTOR_SIZE))

echo "== Building slot ESPs =="
"$DIR/tools/build/build_esp.sh"
cp --reflink=auto --sparse=always "$ESP_IMG" "$ESP_A_IMG"
cp --reflink=auto --sparse=always "$ESP_IMG" "$ESP_B_IMG"
mlabel -i "$ESP_A_IMG" ::VAMOS-A
mlabel -i "$ESP_B_IMG" ::VAMOS-B
for slot in a b; do
  slot_upper="${slot^^}"
  env_path="$GRUBENV_A"
  esp_path="$ESP_A_IMG"
  [ "$slot" = b ] && env_path="$GRUBENV_B" && esp_path="$ESP_B_IMG"
  grub-editenv "$env_path" create
  grub-editenv "$env_path" set \
    generation=1 active=a pending= phase=stable \
    root_a=PARTLABEL=rootfs_a root_b=PARTLABEL=rootfs_b
  mcopy -o -i "$esp_path" "$env_path" ::/EFI/vamos/grubenv
  echo "  prepared VAMOS-$slot_upper"
done

echo "== Building blank inactive rootfs =="
rm -f "$ROOTFS_B_IMG"
truncate -s "$SYSTEM_SIZE" "$ROOTFS_B_IMG"
mkfs.ext4 -F -L VAMOS-B -m 0 -E lazy_itable_init=1,lazy_journal_init=1 "$ROOTFS_B_IMG" >/dev/null

echo "== Building blank persistent userdata =="
rm -f "$USERDATA_IMG"
truncate -s "$USERDATA_SIZE" "$USERDATA_IMG"
# The blank image is also the byte-for-byte baseline for the optional payload.
# Fully initialize ext4 metadata now so mounting its copy cannot trigger lazy
# inode-table writes across the otherwise untouched 99 GiB filesystem.
mkfs.ext4 -F -L VAMOS-DATA -m 0 -E lazy_itable_init=0,lazy_journal_init=0 "$USERDATA_IMG" >/dev/null
mount_image "$USERDATA_IMG"
docker exec "$MOUNT_CONTAINER_ID" touch \
  "$MOUNT_DIR/.vamos-userdata" \
  "$MOUNT_DIR/.vamos-ab-ready"
docker exec "$MOUNT_CONTAINER_ID" chown 1000:1000 "$MOUNT_DIR"
unmount_image
e2fsck -fn "$USERDATA_IMG"

echo "== Building optional openpilot userdata =="
cp --reflink=auto --sparse=always "$USERDATA_IMG" "$USERDATA_OPENPILOT_IMG"
mount_image "$USERDATA_OPENPILOT_IMG"

OP_COMMIT="$(git -C "$OP_SRC" rev-parse HEAD)"
OP_DATE="$(git -C "$OP_SRC" log -1 --format=%ci)"
OP_VERSION="$(sed -n 's/.*COMMA_VERSION[[:space:]]*"\([^"]*\)".*/\1/p' "$OP_SRC/openpilot/common/version.h" | head -1)"
OP_DST="$MOUNT_DIR/openpilot"
CONTINUE="$MOUNT_DIR/continue.sh"
LFS_PATHS="$BUILD_DIR/tmp-disk/openpilot-lfs-paths"
docker exec "$MOUNT_CONTAINER_ID" mkdir -p "$OP_DST"

# Preserve Git and submodule metadata so first boot only performs the normal
# local openpilot build and never needs to download its source. Do not copy
# materialized LFS worktree files here: their bytes already exist in .git/lfs,
# and writing them before replacing them with hardlinks leaves gigabytes of
# duplicate data in freed ext4 blocks that the raw-image delta still carries.
git -C "$OP_SRC" lfs ls-files -n > "$LFS_PATHS"
tar -C "$OP_SRC" --no-wildcards --exclude-from="$LFS_PATHS" -cf - . \
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
  if [ ! -f "$object" ]; then
    echo "ERROR: packaged LFS object is missing for $path ($oid)" >&2
    exit 1
  fi
  mode="$(git -C "$OP_DST" ls-files -s -- "$path" | awk "NR == 1 { print substr(\$1, 4) }")"
  [ -n "$mode" ] || mode=644
  mkdir -p "$(dirname "$worktree")"
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

unmount_image
e2fsck -fn "$USERDATA_OPENPILOT_IMG"

echo "== Assembling common factory disk =="
write_partition "$ESP_A_IMG" "$(partition_start 1)"
write_partition "$ROOTFS_IMG" "$(partition_start 2)"
write_partition "$ESP_B_IMG" "$(partition_start 3)"
write_partition "$ROOTFS_B_IMG" "$(partition_start 4)"
write_partition "$USERDATA_IMG" "$(partition_start 5)"
sgdisk --verify "$DISK_IMG"

jq -n \
  --argjson sector_size "$SECTOR_SIZE" \
  --argjson storage_sectors "$STORAGE_SECTORS" \
  --argjson esp_a_start "$(partition_start 1)" \
  --argjson rootfs_a_start "$(partition_start 2)" \
  --argjson esp_b_start "$(partition_start 3)" \
  --argjson rootfs_b_start "$(partition_start 4)" \
  --argjson userdata_start "$(partition_start 5)" \
  --argjson userdata_sectors "$USERDATA_SECTORS" \
  '{
    sector_size: $sector_size,
    storage_sectors: $storage_sectors,
    partitions: {
      esp_a: {start_sector: $esp_a_start},
      rootfs_a: {start_sector: $rootfs_a_start},
      esp_b: {start_sector: $esp_b_start},
      rootfs_b: {start_sector: $rootfs_b_start},
      userdata: {start_sector: $userdata_start, sectors: $userdata_sectors}
    },
    optional_payloads: {
      openpilot: {
        baseline: "userdata.img",
        image: "userdata-openpilot.img",
        target_partition: "userdata"
      }
    }
  }' > "$LAYOUT_JSON"

docker container rm -f "$MOUNT_CONTAINER_ID" >/dev/null
trap - EXIT

echo "== Done =="
ls -lh "$DISK_IMG" "$USERDATA_IMG" "$USERDATA_OPENPILOT_IMG" "$LAYOUT_JSON"
du -h "$DISK_IMG" "$USERDATA_IMG" "$USERDATA_OPENPILOT_IMG"
sgdisk --print "$DISK_IMG"
