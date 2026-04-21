#!/usr/bin/env bash
# Assemble a bootable disk image for Radxa Dragon Q6A.
# Layout: GPT + ESP (FAT32, kernel Image as BOOTAA64.EFI + dtb) + rootfs (ext4).
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." >/dev/null && pwd)"
cd "$DIR"

BUILD_DIR="$DIR/build"
DISK_IMG="$BUILD_DIR/dragon.img"

KERNEL_IMAGE="$BUILD_DIR/Image"
DTB_FILE="$BUILD_DIR/qcs6490-radxa-dragon-q6a.dtb"
ROOTFS_IMG="$BUILD_DIR/system.img"

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

ESP_SIZE_MB=256
ESP_IMG="$BUILD_DIR/esp.img"

ROOTFS_BYTES=$(stat -c%s "$ROOTFS_IMG")
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
echo "== Building ESP =="
rm -f "$ESP_IMG"
truncate -s $(( ESP_SECTORS * 512 )) "$ESP_IMG"
mkfs.vfat -F 32 -n vamos-ESP "$ESP_IMG" >/dev/null

# Linux arm64 Image is a valid PE32+ EFI application — edk2 launches it directly.
# /EFI/BOOT/BOOTAA64.EFI is the removable-device / fallback boot path edk2 auto-detects.
mmd -i "$ESP_IMG" ::/EFI
mmd -i "$ESP_IMG" ::/EFI/BOOT
mcopy -i "$ESP_IMG" "$KERNEL_IMAGE" ::/EFI/BOOT/BOOTAA64.EFI
mcopy -i "$ESP_IMG" "$DTB_FILE"     ::/qcs6490-radxa-dragon-q6a.dtb
# Also drop the kernel + DTB at the root for convenience / manual boot-manager use.
mcopy -i "$ESP_IMG" "$KERNEL_IMAGE" ::/Image
echo "ESP contents:"
mdir -i "$ESP_IMG" -/ ::/

# ---- Assemble full disk image ----
echo "== Assembling $DISK_IMG =="
rm -f "$DISK_IMG"
truncate -s "$TOTAL_BYTES" "$DISK_IMG"

# GPT + partitions
sgdisk --clear \
       --new=1:${ESP_START}:${ESP_END} \
       --typecode=1:ef00 --change-name=1:esp \
       --new=2:${ROOTFS_START}:${ROOTFS_END} \
       --typecode=2:8300 --change-name=2:rootfs \
       "$DISK_IMG" >/dev/null

# Copy partition contents into the disk image at the right offsets
dd if="$ESP_IMG"    of="$DISK_IMG" bs=512 seek=$ESP_START    conv=notrunc status=none
dd if="$ROOTFS_IMG" of="$DISK_IMG" bs=512 seek=$ROOTFS_START conv=notrunc status=none

echo "== Done =="
ls -lh "$DISK_IMG"
echo ""
sgdisk --print "$DISK_IMG" 2>/dev/null | tail -n +6
