"""Set U-Boot's one-shot normal request through Firehose, without mounting a disk.

Only the existing 1024-byte grubenv files are changed. GPT, FAT chains, directory
entries, adjacent sector contents and A/B trial state remain intact.
"""

from __future__ import annotations

import hashlib
import os
import struct
import tempfile
import time
import uuid
import zlib
from pathlib import Path

from userspace.root.usr.lib.vamos.update import (
    BOOT_PHASE_RANK, GRUB_ENV_SIZE, decode_boot_control, encode_boot_control,
)
from tools.flash.edl_session import FirehoseSession
from tools.flash.devices import select_edl, USB_ROOT


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


class EdlDisk:
    def __init__(self, loader: str, memory: str, max_payload: int | None = None):
        require(memory in ("Ufs", "Spinor"), "unsupported storage")
        self.sector_size = 4096
        payload = int(os.environ.get("VAMOS_EDL_MAX_PAYLOAD", "65536")) if max_payload is None else max_payload
        require(payload >= 4096, "Firehose payload must be at least 4096 bytes")
        executable = Path(__file__).resolve().parents[1] / "bin/edl-ng-dist/edl-ng"
        self.command = ["sudo", "-n", str(executable), f"--maxpayload={payload}",
                        "--vid=05c6", "--pid=9008", f"--memory={memory}",
                        "--slot=0", f"--loader={loader}"]
        self.session: FirehoseSession | None = None
        self.write_bytes = 0
        self.write_seconds = 0.

    def __enter__(self):
        # Refuse to select an arbitrary board when multiple EDL devices exist.
        device = select_edl()
        path = USB_ROOT / device.port
        bus, address = (int((path / name).read_text()) for name in ('busnum', 'devnum'))
        node = f'/dev/bus/usb/{bus:03d}/{address:03d}'
        if os.access(node, os.R_OK | os.W_OK):
            self.command = self.command[2:]
        self.command = [f'--vid={device.vendor:04x}' if value.startswith('--vid=') else
                        f'--pid={device.product_id:04x}' if value.startswith('--pid=') else value
                        for value in self.command]
        self.session = FirehoseSession(self.command)
        try:
            require(self.session.geometry["sector_size"] == self.sector_size,
                    "Firehose storage sector size mismatch")
        except BaseException:
            self.close()
            raise
        return self

    def close(self) -> None:
        if self.session:
            self.session.close()
            self.session = None

    def __exit__(self, *_):
        self.close()

    def _request(self, operation: str, **arguments) -> dict:
        require(self.session is not None, "open EdlDisk with a context manager")
        started = time.monotonic()
        response = self.session.request(operation, **arguments)
        if operation == 'write':
            self.write_bytes += arguments['count'] * self.sector_size
            self.write_seconds += time.monotonic() - started
        return response

    def _verify(self, condition: bool, message: str) -> None:
        if not condition:
            self.close()
            raise ValueError(message)

    def read(self, offset: int, length: int) -> bytes:
        require(offset >= 0 and length > 0, "invalid Firehose read range")
        first = offset // self.sector_size
        count = (offset % self.sector_size + length + self.sector_size - 1) // self.sector_size
        with tempfile.TemporaryDirectory(prefix="vamos-edl-read-") as temporary:
            path = Path(temporary) / "sectors.bin"
            self._request("read", sector=first, count=count, path=str(path))
            data = path.read_bytes()
        self._verify(len(data) == count * self.sector_size, "short Firehose sector read")
        start = offset % self.sector_size
        return data[start:start + length]

    def write_verified(self, offset: int, data: bytes) -> None:
        require(offset % self.sector_size == 0 and len(data) % self.sector_size == 0,
                "unaligned Firehose write")
        with tempfile.TemporaryDirectory(prefix="vamos-edl-write-") as temporary:
            path = Path(temporary) / "sectors.bin"
            path.write_bytes(data)
            self._request("write", sector=offset // self.sector_size,
                          count=len(data) // self.sector_size, path=str(path))
        self._verify(self.read(offset, len(data)) == data, "Firehose boot-control readback mismatch")

    def write_file_verified(self, offset: int, path: Path) -> None:
        """Stream large images and compare a fresh full readback without caching."""
        path = path.resolve()
        size = path.stat().st_size
        require(offset >= 0 and size > 0 and offset % self.sector_size == 0
                and size % self.sector_size == 0, "unaligned Firehose image write")
        sector, count = offset // self.sector_size, size // self.sector_size
        with path.open("rb") as stream:
            expected = hashlib.file_digest(stream, "sha256").digest()
        self._request("write", sector=sector, count=count, path=str(path))
        with tempfile.TemporaryDirectory(prefix="vamos-edl-verify-") as temporary:
            readback = Path(temporary) / "readback.img"
            self._request("read", sector=sector, count=count, path=str(readback))
            self._verify(readback.stat().st_size == size, "short Firehose image readback")
            with readback.open("rb") as stream:
                self._verify(hashlib.file_digest(stream, "sha256").digest() == expected,
                             "Firehose image readback checksum mismatch")

    def erase(self, offset: int, length: int) -> None:
        require(offset >= 0 and length > 0 and offset % self.sector_size == 0
                and length % self.sector_size == 0, "unaligned Firehose erase")
        self._request("erase", sector=offset // self.sector_size, count=length // self.sector_size)

    def reset(self, delay: int) -> None:
        self._request("reset", delay=delay)


def esp_partitions(disk: EdlDisk) -> list[tuple[int, int]]:
    size = disk.sector_size
    header = bytearray(disk.read(size, size))
    require(header[:8] == b"EFI PART", "no primary GPT on the selected storage")
    revision, header_size, crc = struct.unpack_from("<III", header, 8)
    require(revision == 0x10000 and 92 <= header_size <= size, "unsupported GPT header")
    struct.pack_into("<I", header, 16, 0)
    require(zlib.crc32(header[:header_size]) == crc, "GPT header checksum mismatch")
    current, backup, first, last = struct.unpack_from("<QQQQ", header, 24)
    table_lba, count, entry_size, table_crc = struct.unpack_from("<QIII", header, 72)
    require(current == 1 and 2 <= table_lba < first <= last < backup,
            "invalid GPT bounds")
    require(3 <= count <= 4096 and entry_size == 128, "unsupported GPT entry geometry")
    require(table_lba * size + count * entry_size <= first * size,
            "GPT entries overlap partitions")
    entries = disk.read(table_lba * size, count * entry_size)
    require(zlib.crc32(entries) == table_crc, "GPT partition checksum mismatch")
    partitions = []
    for index, label in ((0, "esp_a"), (2, "esp_b")):
        entry = entries[index * entry_size:(index + 1) * entry_size]
        require(uuid.UUID(bytes_le=entry[:16]) == uuid.UUID("c12a7328-f81f-11d2-ba4b-00a0c93ec93b"),
                f"partition {index + 1} is not an ESP")
        require(entry[56:128].decode("utf-16le").rstrip("\0") == label,
                f"partition {index + 1} is not {label}")
        start, end = struct.unpack_from("<QQ", entry, 32)
        require(first <= start <= end <= last, f"{label} lies outside GPT usable space")
        partitions.append((start * size, (end - start + 1) * size))
    require(partitions[0][0] + partitions[0][1] <= partitions[1][0], "overlapping ESPs")
    return partitions


class FatBootControl:
    """Bounded FAT16/FAT32 reader for the fixed short-name EFI/vamos/grubenv path."""

    def __init__(self, disk: EdlDisk, start: int, length: int):
        self.disk, self.start, self.length = disk, start, length
        # One Firehose connection costs much more than these metadata bytes.
        # Snapshot the boot sector, FAT starts and nearby directory entries.
        # Payload reads and write verification always use the device directly.
        self.metadata = disk.read(start, min(length, 128 * 1024))
        boot = self.read_metadata(0, 512)
        require(boot[510:512] == b"\x55\xaa", "invalid FAT boot signature")
        self.bps, spc, reserved, fats, root_entries, total16 = struct.unpack_from("<HBHBHH", boot, 11)
        fat16 = struct.unpack_from("<H", boot, 22)[0]
        total = total16 or struct.unpack_from("<I", boot, 32)[0]
        fat_sectors = fat16 or struct.unpack_from("<I", boot, 36)[0]
        require(self.bps in (512, 1024, 2048, 4096) and self.bps >= disk.sector_size,
                "unsupported FAT sector size")
        require(spc > 0 and spc <= 128 and spc & (spc - 1) == 0 and reserved > 0
                and fats in (1, 2) and fat_sectors > 0 and total * self.bps <= length,
                "invalid FAT geometry")
        self.cluster_size = spc * self.bps
        self.root_offset = (reserved + fats * fat_sectors) * self.bps
        self.root_size = ((root_entries * 32 + self.bps - 1) // self.bps) * self.bps
        self.data_offset = self.root_offset + self.root_size
        self.clusters = (total * self.bps - self.data_offset) // self.cluster_size
        require(4085 <= self.clusters < 0x0ffffff5, "unsupported FAT cluster count")
        self.bits = 16 if self.clusters < 65525 else 32
        require((self.bits == 16) == bool(fat16) and (self.bits == 16) == bool(root_entries),
                "inconsistent FAT type")
        self.root_cluster = struct.unpack_from("<I", boot, 44)[0] if self.bits == 32 else 0
        require(fat_sectors * self.bps >= (self.clusters + 2) * (self.bits // 8),
                "FAT is too short for data area")
        # Read only sectors of the FAT that are actually used by this path.
        self.fat_offsets = [(reserved + i * fat_sectors) * self.bps for i in range(fats)]
        if self.bits == 32:
            flags = struct.unpack_from("<H", boot, 40)[0]
            if flags & 0x80:
                require(flags & 0xf < fats, "invalid active FAT")
                self.fat_offsets = [self.fat_offsets[flags & 0xf]]
        self.fat_cache: dict[int, bytes] = {}
        self.extents = self.find_control()
        require(sum(size for _, size in self.extents) == GRUB_ENV_SIZE, "wrong grubenv size")

    def read(self, offset: int, size: int) -> bytes:
        require(offset >= 0 and size > 0 and offset + size <= self.length,
                "FAT read outside ESP")
        return self.disk.read(self.start + offset, size)

    def read_metadata(self, offset: int, size: int) -> bytes:
        if 0 <= offset and size > 0 and offset + size <= len(self.metadata):
            return self.metadata[offset:offset + size]
        return self.read(offset, size)

    def chain(self, cluster: int):
        seen = set()
        while True:
            require(2 <= cluster < self.clusters + 2 and cluster not in seen,
                    "invalid or cyclic FAT chain")
            require(len(seen) * self.cluster_size < 1024 * 1024, "boot-control FAT chain too long")
            seen.add(cluster)
            yield self.data_offset + (cluster - 2) * self.cluster_size
            entry = cluster * (self.bits // 8)
            values = []
            for fat in self.fat_offsets:
                sector = fat + entry // self.bps * self.bps
                if sector not in self.fat_cache:
                    self.fat_cache[sector] = self.read_metadata(sector, self.bps)
                value = struct.unpack_from("<H" if self.bits == 16 else "<I",
                                           self.fat_cache[sector], entry % self.bps)[0]
                values.append(value & (0xffff if self.bits == 16 else 0x0fffffff))
            require(len(set(values)) == 1, "FAT copies disagree")
            cluster = values[0]
            if cluster >= (0xfff8 if self.bits == 16 else 0x0ffffff8):
                return

    def find_control(self) -> list[tuple[int, int]]:
        directory = [(self.root_offset, self.root_size)] if self.bits == 16 else [
            (offset, self.cluster_size) for offset in self.chain(self.root_cluster)]
        for name, is_directory in ((b"EFI        ", True), (b"VAMOS      ", True), (b"GRUBENV    ", False)):
            matches = []
            ended = False
            for offset, size in directory:
                data = self.read_metadata(offset, size)
                for pos in range(0, len(data), 32):
                    entry = data[pos:pos + 32]
                    if entry[0] == 0:
                        ended = True
                        break
                    if entry[0] == 0xe5 or entry[11] & 8 or entry[:11] != name:
                        continue
                    require(bool(entry[11] & 0x10) == is_directory, "wrong FAT path entry type")
                    cluster = struct.unpack_from("<H", entry, 26)[0]
                    if self.bits == 32:
                        cluster |= struct.unpack_from("<H", entry, 20)[0] << 16
                    matches.append((cluster, struct.unpack_from("<I", entry, 28)[0]))
                if ended:
                    break
            require(len(matches) == 1, f"missing or ambiguous FAT entry {name!r}")
            cluster, file_size = matches[0]
            offsets = list(self.chain(cluster))
            if is_directory:
                directory = [(offset, self.cluster_size) for offset in offsets]
            else:
                require(file_size == GRUB_ENV_SIZE and len(offsets) == (file_size + self.cluster_size - 1) // self.cluster_size,
                        "unexpected grubenv allocation")
                return [(offset, min(self.cluster_size, file_size - i * self.cluster_size))
                        for i, offset in enumerate(offsets)]
        raise ValueError("grubenv not found")

    def payload(self) -> bytes:
        return b"".join(self.read(offset, size) for offset, size in self.extents)

    def write(self, payload: bytes) -> None:
        require(len(payload) == GRUB_ENV_SIZE, "invalid grubenv write size")
        position = 0
        for offset, size in self.extents:
            sector_size = self.disk.sector_size
            begin = offset // sector_size * sector_size
            end = (offset + size + sector_size - 1) // sector_size * sector_size
            original = bytearray(self.read(begin, end - begin))
            original[offset - begin:offset - begin + size] = payload[position:position + size]
            self.disk.write_verified(self.start + begin, bytes(original))
            position += size


def request_normal(disk: EdlDisk) -> dict[str, str | int]:
    # Validate both complete paths and records before the first write. No FAT
    # allocations or repairs are attempted during recovery.
    controls = [FatBootControl(disk, *part) for part in esp_partitions(disk)]
    states = [decode_boot_control(control.payload()) for control in controls]
    chosen = max(states, key=lambda state: (int(state["generation"]), BOOT_PHASE_RANK[str(state["phase"])]))
    require(int(chosen["generation"]) < 2**64 - 2, "boot-control generation exhausted")
    requested = {**chosen, "generation": int(chosen["generation"]) + 1,
                 "normal_request": 1, "edl_request": 0}
    payload = encode_boot_control(requested)
    # A verified newer backup wins even if the primary write is interrupted.
    for control in reversed(controls):
        control.write(payload)
    return requested
