"""Apply the browser's signed U-Boot recipes to a verified NOR backup."""

import hashlib
import lzma
import struct
import zlib

from tools.flash.edl_normal import require
from tools.flash.releases import HASH

NOR_SIZE = 32 * 1024 * 1024
SECTOR = 4096
PARTITIONS = {'XBL', 'XBL_CONFIG', 'AOP', 'TZ', 'DEVCFG', 'HYP', 'QUP', 'TZAPPS', 'CPUCP', 'SHRM', 'PLAT'}


def sha(data):
  return hashlib.sha256(data).hexdigest()


def check_range(offset, size, limit):
  require(type(offset) is int and type(size) is int and offset >= 0 and size > 0 and offset + size <= limit,
          'Invalid firmware range')


def validate(manifest):
  require(manifest.get('manifest_version') == 1 and manifest.get('product') == 'asius-v0'
          and manifest.get('nor_size') == NOR_SIZE, 'Unsupported U-Boot manifest')
  require(HASH.fullmatch(manifest['core_sha256']) is not None, 'Invalid U-Boot core hash')
  require(isinstance(manifest['variants'], list) and 0 < len(manifest['variants']) <= 16, 'Invalid firmware variants')
  for variant in manifest['variants']:
    xbl, template = variant['xbl'], variant['template']
    check_range(xbl['offset'], xbl['size'], NOR_SIZE)
    require(xbl['size'] == 6 * 1024 * 1024 and xbl['offset'] % SECTOR == 0, 'Invalid XBL geometry')
    check_range(0, template['size'], xbl['size'])
    require(template['size'] % SECTOR == 0 and template['url'].startswith('https://'), 'Invalid firmware template')
    require(HASH.fullmatch(template['sha256']) is not None and HASH.fullmatch(variant['result_sha256']) is not None,
            'Invalid firmware hash')
    sources = {}
    require(len(variant['reads']) == len(PARTITIONS), 'Missing boot-chain checks')
    for source in variant['reads']:
      check_range(source['offset'], source['size'], NOR_SIZE)
      require(source['name'] in PARTITIONS and source['name'] not in sources and HASH.fullmatch(source['sha256']) is not None,
              'Invalid firmware input')
      sources[source['name']] = source['size']
    require(len(variant['decode']) <= 1, 'Invalid compressed firmware')
    for decoded in variant['decode']:
      require(decoded['source'] in sources and decoded['name'] not in sources, 'Invalid decompression source')
      check_range(decoded['offset'], decoded['size'], sources[decoded['source']])
      check_range(0, decoded['output_size'], NOR_SIZE)
      require(HASH.fullmatch(decoded['sha256']) is not None, 'Invalid decompressed hash')
      sources[decoded['name']] = decoded['output_size']
    destinations = []
    require(0 < len(variant['copies']) < 64, 'Invalid firmware copies')
    for copy in variant['copies']:
      require(copy['source'] in sources and copy['xor'] in (0, 0x5a), 'Invalid copy source')
      check_range(copy['offset'], copy['size'], sources[copy['source']])
      check_range(copy['target'], copy['size'], template['size'])
      start, end = copy['target'], copy['target'] + copy['size']
      require(all(start >= right or end <= left for left, right in destinations), 'Overlapping firmware copies')
      destinations.append((start, end))


def partitions(nor):
  require(len(nor) == NOR_SIZE, 'Incomplete NOR backup')
  header = bytearray(nor[SECTOR:2 * SECTOR])
  require(header[:8] == b'EFI PART', 'Invalid NOR GPT')
  size, checksum = struct.unpack_from('<II', header, 12)
  struct.pack_into('<I', header, 16, 0)
  require(92 <= size <= SECTOR and zlib.crc32(header[:size]) == checksum, 'NOR GPT checksum mismatch')
  table_lba, count, entry_size, checksum = struct.unpack_from('<QIII', header, 72)
  require(table_lba == 2 and 0 < count <= 128 and entry_size == 128, 'Unsupported NOR GPT')
  table = nor[table_lba * SECTOR:table_lba * SECTOR + count * entry_size]
  require(zlib.crc32(table) == checksum, 'NOR partition checksum mismatch')
  result = {}
  for offset in range(0, len(table), entry_size):
    entry = table[offset:offset + entry_size]
    if not any(entry[:16]):
      continue
    start, end = struct.unpack_from('<QQ', entry, 32)
    name = entry[56:128].decode('utf-16le').rstrip('\0')
    require(name not in result and 6 <= start <= end < NOR_SIZE // SECTOR - 5, 'Invalid NOR partition')
    result[name] = (start * SECTOR, (end - start + 1) * SECTOR)
  return result


def prepare(nor, manifest, download):
  validate(manifest)
  parts = partitions(nor)
  for variant in manifest['variants']:
    xbl = variant['xbl']
    if parts.get('XBL') != (xbl['offset'], xbl['size']):
      continue
    sources = {}
    for source in variant['reads']:
      part = parts.get(source['name'])
      if part is None or part[0] != source['offset'] or (source['size'] > part[1] if source['name'] == 'XBL' else source['size'] != part[1]):
        break
      data = nor[source['offset']:source['offset'] + source['size']]
      if source['name'] != 'XBL' and sha(data) != source['sha256']:
        break
      sources[source['name']] = data
    if len(sources) != len(PARTITIONS):
      continue
    existing = nor[xbl['offset']:xbl['offset'] + xbl['size']]
    if sha(existing[:variant['template']['size']]) == variant['result_sha256']:
      return xbl['offset'], existing, False, sha(nor)
    expected = next(source['sha256'] for source in variant['reads'] if source['name'] == 'XBL')
    if sha(sources['XBL']) != expected:
      continue
    for decoded in variant['decode']:
      source = sources[decoded['source']][decoded['offset']:decoded['offset'] + decoded['size']]
      require(len(source) >= 13 and struct.unpack_from('<I', source, 1)[0] <= NOR_SIZE
              and struct.unpack_from('<Q', source, 5)[0] == decoded['output_size'], 'Invalid LZMA size')
      decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=64 * 1024 * 1024)
      data = decoder.decompress(source, max_length=decoded['output_size'] + 1)
      require(decoder.eof and len(data) == decoded['output_size'] and sha(data) == decoded['sha256'], 'Invalid decompressed firmware')
      sources[decoded['name']] = data
    template = variant['template']
    payload = bytearray(download(template))
    require(len(payload) == template['size'] and sha(payload) == template['sha256'], 'Invalid firmware template')
    for copy in variant['copies']:
      start, end = copy['target'], copy['target'] + copy['size']
      require(not any(payload[start:end]), 'Template contains device firmware')
      source = sources[copy['source']][copy['offset']:copy['offset'] + copy['size']]
      payload[start:end] = bytes(value ^ copy['xor'] for value in source)
    require(sha(payload) == variant['result_sha256'], 'Generated firmware differs from the validated image')
    complete = payload + existing[len(payload):]
    expected_nor = bytearray(nor)
    expected_nor[xbl['offset']:xbl['offset'] + len(complete)] = complete
    return xbl['offset'], bytes(complete), True, sha(expected_nor)
  raise ValueError('This boot firmware revision is not supported. Nothing was written.')
