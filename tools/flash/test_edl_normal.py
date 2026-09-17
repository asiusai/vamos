#!/usr/bin/env python3
"""Exercise sector updates against filesystems built by dosfstools/mtools."""

import hashlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import uuid
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.flash.edl_normal import FatBootControl, esp_partitions, request_normal
from userspace.root.usr.lib.vamos.update import decode_boot_control, encode_boot_control


class ImageDisk:
    def __init__(self, directory, sector_size=4096, fat_bits=16):
        self.sector_size = sector_size
        self.writes = []
        self.parts = []
        self.tables = bytearray(34 * sector_size)
        entries = bytearray(128 * 128)
        for index in range(2):
            size = (256 if sector_size == 4096 else 64) * 1024 * 1024
            start = 1024 * 1024 + index * (size + 1024 * 1024)
            path = directory / f"esp-{index}.img"
            with path.open("wb") as output:
                output.truncate(size)
            subprocess.run(["mkfs.vfat", "-F", str(fat_bits), "-S", str(sector_size), str(path)],
                           check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["mmd", "-i", str(path), "::/EFI", "::/EFI/vamos"], check=True)
            state = {"generation": 7, "active": "a", "pending": "b", "phase": "armed" if index == 0 else "attempted",
                     "root_a": "PARTLABEL=rootfs_a", "root_b": "PARTLABEL=rootfs_b",
                     "edl_request": 1, "normal_request": 0, "usb_mode": "ncm"}
            payload = directory / "grubenv"
            payload.write_bytes(encode_boot_control(state))
            subprocess.run(["mcopy", "-i", str(path), str(payload), "::/EFI/vamos/grubenv"], check=True)
            self.parts.append((start, size, path))
            offset = index * 2 * 128
            entries[offset:offset + 16] = uuid.UUID("c12a7328-f81f-11d2-ba4b-00a0c93ec93b").bytes_le
            struct.pack_into("<QQ", entries, offset + 32, start // sector_size, (start + size) // sector_size - 1)
            name = ("esp_a" if index == 0 else "esp_b").encode("utf-16le")
            entries[offset + 56:offset + 56 + len(name)] = name
        self.tables[2 * sector_size:2 * sector_size + len(entries)] = entries
        header = bytearray(sector_size)
        last = (self.parts[-1][0] + self.parts[-1][1]) // sector_size
        struct.pack_into("<8sIIIIQQQQ16sQIII", header, 0, b"EFI PART", 0x10000, 92, 0, 0,
                         1, last + 34, 34, last + 1, bytes(16), 2, 128, 128, zlib.crc32(entries))
        struct.pack_into("<I", header, 16, zlib.crc32(header[:92]))
        self.tables[sector_size:2 * sector_size] = header

    def read(self, offset, size):
        if offset < len(self.tables):
            return bytes(self.tables[offset:offset + size])
        for start, length, path in self.parts:
            if start <= offset and offset + size <= start + length:
                with path.open("rb") as source:
                    source.seek(offset - start)
                    return source.read(size)
        raise AssertionError("read outside fixture")

    def write_verified(self, offset, data):
        assert offset % self.sector_size == 0 and len(data) % self.sector_size == 0
        for start, length, path in self.parts:
            if start <= offset and offset + len(data) <= start + length:
                self.writes.append((offset, data))
                with path.open("r+b") as output:
                    output.seek(offset - start)
                    output.write(data)
                assert self.read(offset, len(data)) == data
                return
        raise AssertionError("write outside fixture")


@unittest.skipUnless(all(shutil.which(tool) for tool in ("mkfs.vfat", "mmd", "mcopy")), "requires dosfstools and mtools")
class NormalBootTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_real_fat_filesystems_and_sector_preservation(self):
        for sector, bits in ((4096, 16), (512, 16), (512, 32)):
            with self.subTest(sector=sector, bits=bits):
                disk = ImageDisk(self.directory, sector, bits)
                controls = [FatBootControl(disk, *part) for part in esp_partitions(disk)]
                before = [control.payload() for control in controls]
                hashes = [hashlib.sha256(path.read_bytes()).digest() for _, _, path in disk.parts]
                state = request_normal(disk)
                self.assertEqual(state["phase"], "attempted")
                self.assertEqual(state["pending"], "b")
                self.assertEqual(state["generation"], 8)
                self.assertEqual(state["edl_request"], 0)
                self.assertEqual(state["normal_request"], 1)
                self.assertGreaterEqual(disk.writes[0][0], disk.parts[1][0])
                for i, control in enumerate(controls):
                    extracted = self.directory / "extracted"
                    subprocess.run(["mcopy", "-o", "-i", str(disk.parts[i][2]),
                                    "::/EFI/vamos/grubenv", str(extracted)], check=True)
                    self.assertEqual(decode_boot_control(extracted.read_bytes()), state)
                    # Revert just the original file bytes independently. The
                    # whole ESP must then have its exact original digest.
                    cursor = 0
                    with disk.parts[i][2].open("r+b") as output:
                        for offset, size in control.extents:
                            output.seek(offset)
                            output.write(before[i][cursor:cursor + size])
                            cursor += size
                    self.assertEqual(hashlib.sha256(disk.parts[i][2].read_bytes()).digest(), hashes[i])

    def test_bad_gpt_or_record_never_writes(self):
        disk = ImageDisk(self.directory)
        disk.tables[disk.sector_size + 16] ^= 1
        with self.assertRaisesRegex(ValueError, "checksum"):
            request_normal(disk)
        self.assertEqual(disk.writes, [])
        disk.tables[disk.sector_size + 16] ^= 1
        second = FatBootControl(disk, *esp_partitions(disk)[1])
        with disk.parts[1][2].open("r+b") as output:
            output.seek(second.extents[0][0])
            output.write(b"corrupt")
        with self.assertRaisesRegex(Exception, "boot-control"):
            request_normal(disk)
        self.assertEqual(disk.writes, [])

    def test_interrupted_primary_keeps_newer_verified_backup(self):
        disk = ImageDisk(self.directory)
        original_write = disk.write_verified
        def fail_primary(offset, payload):
            if offset < disk.parts[1][0]:
                raise RuntimeError("USB disconnected")
            original_write(offset, payload)
        disk.write_verified = fail_primary
        with self.assertRaisesRegex(RuntimeError, "USB disconnected"):
            request_normal(disk)
        states = [decode_boot_control(FatBootControl(disk, *part).payload()) for part in esp_partitions(disk)]
        self.assertEqual(states[0]["generation"], 7)
        self.assertEqual(states[1]["generation"], 8)
        self.assertEqual(states[1]["normal_request"], 1)

    def test_cyclic_fat_chain_never_writes(self):
        disk = ImageDisk(self.directory)
        control = FatBootControl(disk, *esp_partitions(disk)[0])
        cluster = (control.extents[0][0] - control.data_offset) // control.cluster_size + 2
        with disk.parts[0][2].open("r+b") as output:
            for fat_offset in control.fat_offsets:
                output.seek(fat_offset + cluster * 2)
                output.write(struct.pack("<H", cluster))
        with self.assertRaisesRegex(ValueError, "cyclic"):
            request_normal(disk)
        self.assertEqual(disk.writes, [])


if __name__ == "__main__":
    unittest.main()
