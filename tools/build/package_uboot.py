#!/usr/bin/env python3
"""Create signed-manifest inputs without distributing device-derived firmware.

Reference NOR dumps remain private. Public templates contain U-Boot and image
headers only. Copy instructions recover every proprietary segment from the
connected device after exact firmware checks. Unrecognized firmware is refused.
"""
import argparse
import hashlib
import json
import lzma
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'bootloader/qtestsign'))
from mbn.elf import Elf  # noqa: E402
from embed_secure_launch import BLOB_OFFSET, embedded_firmware, fat12_file, validate_tcb, validate_mssecapp, validate_acpi  # noqa: E402

SECTOR = 4096
NOR_SIZE = 32 * 1024 * 1024
# These partitions supply the boot chain and Secure Launch dependencies.
CHECK_PARTITIONS = ('XBL_CONFIG', 'AOP', 'TZ', 'DEVCFG', 'HYP', 'QUP', 'TZAPPS', 'CPUCP', 'SHRM', 'PLAT')


def sha(data):
  return hashlib.sha256(data).hexdigest()


def partitions(nor):
  if len(nor) != NOR_SIZE:
    raise ValueError('Expected a complete 32 MiB NOR backup')
  header = bytearray(nor[SECTOR:2 * SECTOR])
  if header[:8] != b'EFI PART':
    raise ValueError('Invalid NOR GPT')
  size, checksum = struct.unpack_from('<II', header, 12)
  struct.pack_into('<I', header, 16, 0)
  if not 92 <= size <= SECTOR or zlib.crc32(header[:size]) != checksum:
    raise ValueError('Invalid GPT checksum')
  table_lba, count, entry_size, checksum = struct.unpack_from('<QIII', header, 72)
  if table_lba != 2 or not 1 <= count <= 128 or entry_size != 128:
    raise ValueError('Unsupported NOR GPT')
  table = nor[table_lba * SECTOR:table_lba * SECTOR + count * entry_size]
  if zlib.crc32(table) != checksum:
    raise ValueError('Invalid GPT partition checksum')
  result = {}
  for offset in range(0, len(table), entry_size):
    entry = table[offset:offset + entry_size]
    if not any(entry[:16]):
      continue
    start, end = struct.unpack_from('<QQ', entry, 32)
    name = entry[56:128].decode('utf-16le').rstrip('\0')
    if name in result or not 6 <= start <= end < NOR_SIZE // SECTOR - 5:
      raise ValueError('Invalid partition geometry')
    result[name] = (start * SECTOR, (end - start + 1) * SECTOR)
  return result


def elf_size(data):
  return (max(p.p_offset + p.p_filesz for p in Elf.parse(data).phdrs) + SECTOR - 1) // SECTOR * SECTOR


def generate(reference, core, output, base_url):
  nor = reference.read_bytes()
  parts = partitions(nor)
  xbl_offset, xbl_size = parts['XBL']
  if xbl_size != 6 * 1024 * 1024:
    raise ValueError('Unsupported XBL partition size')
  sources = {name: nor[start:start + size] for name, (start, size) in parts.items()}
  xbl = sources['XBL']
  input_size = elf_size(xbl)
  reads = [{'name': name, 'offset': parts[name][0], 'size': parts[name][1], 'sha256': sha(sources[name])}
           for name in CHECK_PARTITIONS]
  reads.insert(0, {'name': 'XBL', 'offset': xbl_offset, 'size': input_size, 'sha256': sha(xbl[:input_size])})
  decoded = []
  with tempfile.TemporaryDirectory() as temporary:
    work = Path(temporary)
    original = work / 'xbl.bin'
    original.write_bytes(xbl)
    try:
      tcb, mssecapp, acpi = embedded_firmware(xbl)
    except ValueError:
      subprocess.run(['uefiextract', str(original), 'all'], check=True, stdout=subprocess.DEVNULL)
      resources = list(work.glob('**/*resource.bin/1 Raw section/body.bin'))
      if len(resources) != 1:
        raise ValueError('Expected exactly one Secure Launch resource')
      resource = resources[0].read_bytes()
      # Locate the enclosing LZMA section using extraction metadata. The browser
      # decodes only this hash-checked, size-limited section; it needs no UEFI parser.
      for parent in resources[0].parents:
        info = parent / 'info.txt'
        body = parent / 'body.bin'
        if info.is_file() and body.is_file() and 'Compression algorithm: LZMA' in info.read_text():
          compressed = body.read_bytes()
          position = xbl.find(compressed)
          if position < 0:
            continue
          volume = lzma.decompress(compressed, format=lzma.FORMAT_ALONE)
          if resource not in volume:
            continue
          sources['volume'] = volume
          decoded.append({'name': 'volume', 'source': 'XBL', 'offset': position, 'size': len(compressed),
                          'output_size': len(volume), 'sha256': sha(volume)})
          break
      else:
        raise ValueError('Cannot locate the stock Secure Launch compressed volume')
      tcb = bytes(b ^ 0x5a for b in resource)
      mssecapp = fat12_file(sources['TZAPPS'], 'TZAPPS/MSSECAPP.MBN')
      acpi = fat12_file(sources['PLAT'], 'ACPI/ACPI.ELF')
    validate_tcb(tcb)
    validate_mssecapp(mssecapp)
    validate_acpi(acpi)
    core_bytes = core.read_bytes()
    if len(core_bytes) > BLOB_OFFSET:
      raise ValueError('U-Boot overlaps Secure Launch data')
    blob = struct.pack('<8sIIII', b'VAMOS-SL', 3, len(tcb), len(mssecapp), len(acpi))
    combined = core_bytes + bytes(BLOB_OFFSET - len(core_bytes)) + blob + tcb + mssecapp + acpi
    (work / 'core.bin').write_bytes(combined)
    subprocess.run([sys.executable, str(ROOT / 'bootloader/qtestsign/patchxbl.py'), str(original), '-c', str(work / 'core.bin'), '-o', str(work / 'unsigned')], check=True, stdout=subprocess.DEVNULL)
    subprocess.run([sys.executable, str(ROOT / 'bootloader/qtestsign/qtestsign.py'), '-v6', 'sbl1', str(work / 'unsigned'), '-o', str(work / 'signed')], check=True, stdout=subprocess.DEVNULL)
    signed = (work / 'signed').read_bytes()
    signed += bytes((-len(signed)) % SECTOR)
    if len(signed) > xbl_size:
      raise ValueError('U-Boot exceeds XBL')
    template = bytearray(signed)
    copies = []

    def copy(name, source_offset, target, size, xor=0):
      content = bytes(b ^ xor for b in sources[name][source_offset:source_offset + size])
      if len(content) != size or content != signed[target:target + size]:
        raise ValueError('Firmware copy differs from the CLI-generated image')
      copies.append({'source': name, 'offset': source_offset, 'target': target, 'size': size, 'xor': xor})
      template[target:target + size] = bytes(size)

    old = Elf.parse(xbl)
    for segment in Elf.parse(signed).phdrs:
      if segment.p_type != 1 or not segment.p_filesz:
        continue
      if segment.p_paddr != 0x9f000000:
        original_segment = next(p for p in old.phdrs if p.p_paddr == segment.p_paddr and p.p_filesz == segment.p_filesz)
        copy('XBL', original_segment.p_offset, segment.p_offset, segment.p_filesz)
      else:
        target = segment.p_offset + BLOB_OFFSET + len(blob)
        for payload in (tcb, mssecapp, acpi):
          candidates = [('XBL', payload, 0), ('TZAPPS', payload, 0), ('PLAT', payload, 0)]
          if 'volume' in sources:
            candidates.append(('volume', bytes(b ^ 0x5a for b in payload), 0x5a))
          for name, search, xor in candidates:
            position = sources[name].find(search)
            if position >= 0:
              copy(name, position, target, len(payload), xor)
              break
          else:
            raise ValueError('Firmware payload is not a contiguous source range')
          target += len(payload)
    # The public file must not contain any firmware payload in cleartext.
    for payload in (tcb, mssecapp, acpi):
      if payload in template:
        raise ValueError('Device firmware leaked into the public template')
    digest = sha(template)
    filename = f'uboot-{digest}.bin'
    (output / filename).write_bytes(template)
    return {'label': reference.stem, 'reads': reads, 'decode': decoded, 'copies': copies,
            'xbl': {'offset': xbl_offset, 'size': xbl_size},
            'template': {'url': f'{base_url}/{filename}', 'size': len(template), 'sha256': digest},
            'result_sha256': sha(signed)}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--reference', type=Path, action='append', required=True)
  parser.add_argument('--core', type=Path, required=True)
  parser.add_argument('--output', type=Path, required=True)
  parser.add_argument('--base-url', default='https://vamos.asius.ai/bootloader/objects')
  args = parser.parse_args()
  args.output.mkdir(parents=True, exist_ok=True)
  variants = [generate(path, args.core, args.output, args.base_url) for path in args.reference]
  manifest = {'manifest_version': 1, 'product': 'asius-v0', 'nor_size': NOR_SIZE,
              'core_sha256': sha(args.core.read_bytes()), 'variants': variants}
  (args.output / 'uboot.json').write_text(json.dumps(manifest, indent=2) + '\n')
  print(f'Packaged {len(variants)} firmware variants; private NOR files were not copied')


if __name__ == '__main__':
  main()
