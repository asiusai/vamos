#!/usr/bin/env python3
"""Embed device-derived Secure Launch firmware in a generated U-Boot core."""

import argparse
import hashlib
import struct
from pathlib import Path


BLOB_OFFSET = 0x180000
XBL_CORE_SIZE = 0x374000
BLOB_MAGIC = b"VAMOS-SL"
BLOB_VERSION = 3
MSSECAPP_PATH = "TZAPPS/MSSECAPP.MBN"
ACPI_PATH = "ACPI/ACPI.ELF"


def u16(data: bytes, offset: int) -> int:
  return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
  return struct.unpack_from("<I", data, offset)[0]


def checked_range(data: bytes, offset: int, size: int, label: str) -> None:
  if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
    raise ValueError(f"{label} falls outside the {len(data)}-byte resource")


def fat12_file(image: bytes, path: str) -> bytes:
  """Read one 8.3 file from a small firmware FAT12 partition."""
  checked_range(image, 0, 64, "FAT boot sector")
  bytes_per_sector = u16(image, 11)
  sectors_per_cluster = image[13]
  reserved_sectors = u16(image, 14)
  fat_count = image[16]
  root_entries = u16(image, 17)
  sectors_per_fat = u16(image, 22)
  if bytes_per_sector < 512 or bytes_per_sector & (bytes_per_sector - 1):
    raise ValueError("invalid FAT sector size")
  if not sectors_per_cluster or not reserved_sectors or not fat_count or not sectors_per_fat:
    raise ValueError("invalid FAT12 geometry")

  fat_offset = reserved_sectors * bytes_per_sector
  fat_size = sectors_per_fat * bytes_per_sector
  root_offset = (reserved_sectors + fat_count * sectors_per_fat) * bytes_per_sector
  root_size = root_entries * 32
  root_sectors = (root_size + bytes_per_sector - 1) // bytes_per_sector
  data_offset = root_offset + root_sectors * bytes_per_sector
  cluster_size = sectors_per_cluster * bytes_per_sector
  checked_range(image, fat_offset, fat_size, "FAT")
  checked_range(image, root_offset, root_size, "root directory")

  def next_cluster(cluster: int) -> int:
    offset = cluster + cluster // 2
    checked_range(image, fat_offset + offset, 2, "FAT12 cluster entry")
    value = u16(image, fat_offset + offset)
    return value >> 4 if cluster & 1 else value & 0xFFF

  def cluster_data(cluster: int) -> bytes:
    if cluster < 2:
      raise ValueError(f"invalid FAT12 cluster {cluster}")
    offset = data_offset + (cluster - 2) * cluster_size
    checked_range(image, offset, cluster_size, "FAT12 cluster")
    return image[offset:offset + cluster_size]

  def chain_data(first_cluster: int) -> bytes:
    output = bytearray()
    cluster = first_cluster
    seen = set()
    while cluster < 0xFF8:
      if cluster in seen or len(seen) > len(image) // max(cluster_size, 1):
        raise ValueError("FAT12 cluster chain loops")
      seen.add(cluster)
      output.extend(cluster_data(cluster))
      cluster = next_cluster(cluster)
      if cluster == 0xFF7:
        raise ValueError("FAT12 cluster chain contains a bad cluster")
    return bytes(output)

  def entries(directory: bytes):
    for offset in range(0, len(directory) - 31, 32):
      entry = directory[offset:offset + 32]
      if entry[0] == 0:
        break
      if entry[0] == 0xE5 or entry[11] == 0x0F or entry[11] & 0x08:
        continue
      base = entry[:8].decode("ascii", errors="strict").rstrip()
      extension = entry[8:11].decode("ascii", errors="strict").rstrip()
      name = base + (f".{extension}" if extension else "")
      yield name.upper(), entry

  directory = image[root_offset:root_offset + root_size]
  components = [component.upper() for component in path.strip("/").split("/")]
  for index, component in enumerate(components):
    match = next((entry for name, entry in entries(directory) if name == component), None)
    if match is None:
      raise ValueError(f"{path} is missing from the FAT12 image")
    is_directory = bool(match[11] & 0x10)
    first_cluster = u16(match, 26)
    if index != len(components) - 1:
      if not is_directory:
        raise ValueError(f"{component} in {path} is not a directory")
      directory = chain_data(first_cluster)
    else:
      if is_directory:
        raise ValueError(f"{path} is a directory")
      size = u32(match, 28)
      data = chain_data(first_cluster)
      if size > len(data):
        raise ValueError(f"{path} is truncated")
      return data[:size]

  raise ValueError(f"invalid empty FAT path: {path}")


def validate_tcb(data: bytes) -> None:
  checked_range(data, 0, 0x40, "DOS header")
  if data[:2] != b"MZ":
    raise ValueError("decoded resource is not a PE image")

  pe_offset = u32(data, 0x3C)
  checked_range(data, pe_offset, 24, "PE header")
  if data[pe_offset:pe_offset + 4] != b"PE\0\0":
    raise ValueError("decoded resource has no PE signature")

  file_header = pe_offset + 4
  if u16(data, file_header) != 0xAA64:
    raise ValueError("Secure Launch TCB is not AArch64")
  section_count = u16(data, file_header + 2)
  optional_size = u16(data, file_header + 16)
  optional = file_header + 20
  checked_range(data, optional, optional_size, "optional header")
  if optional_size < 152 or u16(data, optional) != 0x20B:
    raise ValueError("Secure Launch TCB is not PE32+")
  if u16(data, optional + 68) != 16:
    raise ValueError("PE subsystem is not Windows boot application")

  headers_size = u32(data, optional + 60)
  image_size = u32(data, optional + 56)
  checked_range(data, 0, headers_size, "PE headers")
  if not image_size or image_size > 2 * 1024 * 1024:
    raise ValueError(f"unexpected loaded PE size: {image_size}")

  section_table = optional + optional_size
  checked_range(data, section_table, section_count * 40, "section table")
  for index in range(section_count):
    section = section_table + index * 40
    virtual_address = u32(data, section + 12)
    raw_size = u32(data, section + 16)
    raw_offset = u32(data, section + 20)
    checked_range(data, raw_offset, raw_size, f"section {index}")
    if virtual_address > 2 * 1024 * 1024 or raw_size > 2 * 1024 * 1024 - virtual_address:
      raise ValueError(f"section {index} exceeds the 2 MiB loaded image")

  security = optional + 112 + 4 * 8
  security_offset = u32(data, security)
  security_size = u32(data, security + 4)
  checked_range(data, security_offset, security_size, "certificate")
  if security_size < 8 or u16(data, security_offset + 4) != 0x200 or u16(data, security_offset + 6) != 2:
    raise ValueError("PE certificate is not a PKCS#7 WIN_CERTIFICATE")


def validate_mssecapp(data: bytes) -> None:
  checked_range(data, 0, 64, "mssecapp ELF header")
  if data[:7] != b"\x7fELF\x02\x01\x01":
    raise ValueError("mssecapp is not a 64-bit little-endian ELF image")
  if u16(data, 18) != 0xB7:
    raise ValueError("mssecapp is not AArch64")
  if len(data) > 2 * 1024 * 1024:
    raise ValueError(f"unexpected mssecapp size: {len(data)}")


def validate_acpi(data: bytes) -> tuple[int, int]:
  checked_range(data, 0, 52, "ACPI ELF header")
  if data[:7] != b"\x7fELF\x01\x01\x01":
    raise ValueError("ACPI firmware is not a 32-bit little-endian ELF image")
  if u16(data, 18) != 0x28:
    raise ValueError("ACPI firmware is not ARM")

  program_offset = u32(data, 28)
  program_size = u16(data, 42)
  program_count = u16(data, 44)
  if program_size < 32 or not program_count:
    raise ValueError("ACPI firmware has no valid program headers")
  checked_range(data, program_offset, program_size * program_count,
                "ACPI program headers")

  metadata_end = program_offset + program_size * program_count
  hash_segments = []
  load_segments = []
  for index in range(program_count):
    program = program_offset + index * program_size
    program_type = u32(data, program)
    flags = u32(data, program + 24)
    segment_type = (flags & 0x07000000) >> 24
    if program_type in (0, 1) and segment_type == 2:
      hash_offset = u32(data, program + 4)
      hash_size = u32(data, program + 16)
      checked_range(data, hash_offset, hash_size,
                    f"ACPI hash segment {index}")
      checked_range(data, metadata_end, hash_size,
                    "compacted ACPI PIL metadata")
      if not hash_size:
        raise ValueError(f"ACPI hash segment {index} is empty")
      hash_segments.append((hash_offset, hash_size))
      continue
    if program_type != 1:
      continue
    load_offset = u32(data, program + 4)
    load_size = u32(data, program + 16)
    memory_size = u32(data, program + 20)
    checked_range(data, load_offset, load_size, f"ACPI LOAD segment {index}")
    if not load_size or memory_size < load_size:
      raise ValueError(f"ACPI LOAD segment {index} has an invalid size")
    load_segments.append((load_offset, load_size))

  if len(hash_segments) != 1:
    raise ValueError(
      f"expected one ACPI hash segment, found {len(hash_segments)}"
    )
  if len(load_segments) != 1:
    raise ValueError(
      f"expected one ACPI LOAD segment, found {len(load_segments)}"
    )
  load_offset, load_size = load_segments[0]
  if data[load_offset:load_offset + 4] != b"ACPI":
    raise ValueError("ACPI LOAD segment has no ACPI firmware header")
  if len(data) > 1024 * 1024:
    raise ValueError(f"unexpected ACPI firmware size: {len(data)}")
  return load_offset, load_size


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("--core", type=Path, required=True)
  parser.add_argument("--resource", type=Path, required=True,
                      help="XOR-0x5a resource.bin raw section from stock XBL")
  parser.add_argument("--tzapps", type=Path, required=True,
                      help="device TZAPPS partition containing mssecapp.mbn")
  parser.add_argument("--plat", type=Path, required=True,
                      help="device PLAT partition containing ACPI/ACPI.elf")
  parser.add_argument("--output", type=Path, required=True)
  args = parser.parse_args()

  core = args.core.read_bytes()
  if len(core) > BLOB_OFFSET:
    raise ValueError(
      f"U-Boot core is {len(core)} bytes and overlaps blob offset 0x{BLOB_OFFSET:x}"
    )

  encoded = args.resource.read_bytes()
  tcb = bytes(byte ^ 0x5A for byte in encoded)
  validate_tcb(tcb)
  mssecapp = fat12_file(args.tzapps.read_bytes(), MSSECAPP_PATH)
  validate_mssecapp(mssecapp)
  acpi = fat12_file(args.plat.read_bytes(), ACPI_PATH)
  acpi_load_offset, acpi_load_size = validate_acpi(acpi)

  header = struct.pack(
    "<8sIIII", BLOB_MAGIC, BLOB_VERSION, len(tcb), len(mssecapp), len(acpi)
  )
  output = core + bytes(BLOB_OFFSET - len(core)) + header + tcb + mssecapp + acpi
  if len(output) > XBL_CORE_SIZE:
    raise ValueError(
      f"generated core is {len(output)} bytes; XBL segment is {XBL_CORE_SIZE} bytes"
    )

  args.output.parent.mkdir(parents=True, exist_ok=True)
  args.output.write_bytes(output)
  print(
    f"Embedded {len(tcb)}-byte Secure Launch TCB at 0x{BLOB_OFFSET + len(header):x}; "
    f"sha256 {hashlib.sha256(tcb).hexdigest()}"
  )
  print(
    f"Embedded {len(mssecapp)}-byte mssecapp at "
    f"0x{BLOB_OFFSET + len(header) + len(tcb):x}; "
    f"sha256 {hashlib.sha256(mssecapp).hexdigest()}"
  )
  print(
    f"Embedded {len(acpi)}-byte ACPI firmware at "
    f"0x{BLOB_OFFSET + len(header) + len(tcb) + len(mssecapp):x}; "
    f"LOAD +0x{acpi_load_offset:x}/0x{acpi_load_size:x}; "
    f"sha256 {hashlib.sha256(acpi).hexdigest()}"
  )
  print(f"Generated {args.output} ({len(output)} / {XBL_CORE_SIZE} bytes)")


if __name__ == "__main__":
  main()
