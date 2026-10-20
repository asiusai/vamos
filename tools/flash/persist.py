"""Protect the fixed identity region, including retries after GPT erasure."""

import struct
import zlib


def initialize_persist(read, sector_size, persist):
  """Return True only when a valid old GPT proves no persist partition exists."""
  offset, size = persist["offset"], persist["size"]
  header = bytearray(read(sector_size, sector_size))
  if not any(header):
    # A blank GPT can also be an interrupted flash. Never erase identity here.
    return False
  if header[:8] != b"EFI PART":
    raise ValueError("Invalid GPT; refusing to risk the device identity")
  revision, header_size, checksum = struct.unpack_from("<III", header, 8)
  if revision != 0x10000 or not 92 <= header_size <= sector_size:
    raise ValueError("Unsupported GPT header")
  struct.pack_into("<I", header, 16, 0)
  if zlib.crc32(header[:header_size]) != checksum:
    raise ValueError("GPT header checksum mismatch")
  table_lba, count, entry_size, table_crc = struct.unpack_from("<QIII", header, 72)
  if table_lba != 2 or not 6 <= count <= 128 or entry_size != 128:
    raise ValueError("Unsupported GPT entries")
  entries = read(table_lba * sector_size, count * entry_size)
  if zlib.crc32(entries) != table_crc:
    raise ValueError("GPT partition checksum mismatch")
  found = 0
  for index in range(count):
    entry = entries[index * entry_size:(index + 1) * entry_size]
    if not any(entry[:16]):
      continue
    if entry[56:128].decode("utf-16-le").rstrip("\0") == "persist":
      start, end = struct.unpack_from("<QQ", entry, 32)
      if index != 5 or start * sector_size != offset or (end - start + 1) * sector_size != size:
        raise ValueError("Existing persist geometry differs; explicit migration is required")
      found += 1
  if found:
    return False
  # Preserve a provisioned filesystem even if GPT restoration used an old table.
  superblock = read(offset + 1024, 1024)
  return not (superblock[56:58] == b"\x53\xef" and superblock[120:136].rstrip(b"\0") == b"VAMOS-IDENTITY")
