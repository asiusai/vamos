import json
import os
import struct
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlparse

from tools.flash.uboot_recipe import (
  NOR_SIZE,
  PARTITIONS,
  SECTOR,
  prepare,
  sha,
  validate,
)


def fixture():
  nor = bytearray(NOR_SIZE)
  reads = []
  for index, name in enumerate(['XBL', *sorted(PARTITIONS - {'XBL'})]):
    start = 256 if index == 0 else 1792 + index
    size = 1536 if index == 0 else 1
    entry = 8192 + index * 128
    nor[entry] = 1
    struct.pack_into('<QQ', nor, entry + 32, start, start + size - 1)
    encoded = name.encode('utf-16le')
    nor[entry + 56:entry + 56 + len(encoded)] = encoded
    nor[start * SECTOR:(start + 1) * SECTOR] = bytes([index + 1]) * SECTOR
    reads.append({'name': name, 'offset': start * SECTOR, 'size': SECTOR,
                  'sha256': sha(nor[start * SECTOR:(start + 1) * SECTOR])})
  nor[SECTOR:SECTOR + 8] = b'EFI PART'
  struct.pack_into('<I', nor, SECTOR + 12, 92)
  struct.pack_into('<QIII', nor, SECTOR + 72, 2, 128, 128, zlib.crc32(nor[8192:8192 + 128 * 128]))
  struct.pack_into('<I', nor, SECTOR + 16, zlib.crc32(nor[SECTOR:SECTOR + 92]))
  template = b'*' + bytes(SECTOR - 1)
  expected = bytearray(template)
  expected[64:80] = bytes([1]) * 16
  manifest = {'manifest_version': 1, 'product': 'asius-v0', 'nor_size': NOR_SIZE, 'core_sha256': 'a' * 64,
              'variants': [{'label': 'test', 'reads': reads, 'decode': [],
                            'xbl': {'offset': 256 * SECTOR, 'size': 6 * 1024 * 1024},
                            'template': {'url': 'https://example.test/uboot.bin', 'size': len(template), 'sha256': sha(template)},
                            'result_sha256': sha(expected),
                            'copies': [{'source': 'XBL', 'offset': 100, 'size': 16, 'target': 64, 'xor': 0}]}]}
  return nor, manifest, template, expected


class UbootRecipeTest(unittest.TestCase):
  def test_preserves_xbl_tail_and_every_other_nor_byte(self):
    nor, manifest, template, expected = fixture()
    before = bytes(nor)
    offset, payload, changed, digest = prepare(nor, manifest, lambda _: template)
    self.assertTrue(changed)
    self.assertEqual(payload[:len(expected)], expected)
    self.assertEqual(payload[len(expected):], nor[offset + len(expected):offset + len(payload)])
    self.assertEqual(nor, before)
    nor[offset:offset + len(payload)] = payload
    self.assertEqual(sha(nor), digest)
    self.assertEqual(nor[:offset], before[:offset])
    self.assertEqual(nor[offset + len(payload):], before[offset + len(payload):])
    download = Mock(side_effect=AssertionError('No download expected'))
    self.assertFalse(prepare(nor, manifest, download)[2])
    download.assert_not_called()

  def test_unknown_firmware_is_rejected_before_downloading(self):
    nor, manifest, _, _ = fixture()
    nor[manifest['variants'][0]['reads'][4]['offset']] ^= 1
    download = Mock()
    with self.assertRaisesRegex(ValueError, 'not supported'):
      prepare(nor, manifest, download)
    download.assert_not_called()

  def test_invalid_gpt_template_and_result_hashes_are_rejected(self):
    for failure in ('gpt', 'template', 'result'):
      nor, manifest, template, _ = fixture()
      if failure == 'gpt':
        nor[8192] ^= 1
      elif failure == 'template':
        template = b'!' + template[1:]
      else:
        manifest['variants'][0]['result_sha256'] = '0' * 64
      with self.subTest(failure=failure), self.assertRaises(ValueError):
        prepare(nor, manifest, lambda _, value=template: value)

  def test_invalid_copy_and_duplicate_source_are_rejected(self):
    for failure in ('range', 'duplicate'):
      _, manifest, _, _ = fixture()
      if failure == 'range':
        manifest['variants'][0]['copies'][0]['target'] = SECTOR
      else:
        manifest['variants'][0]['reads'][1]['name'] = 'XBL'
      with self.subTest(failure=failure), self.assertRaises(ValueError):
        validate(manifest)

  @unittest.skipUnless(os.environ.get('UBOOT_REFERENCE_DIR'), 'Private firmware fixtures are not configured')
  def test_matches_browser_reference_images(self):
    root = Path(os.environ['UBOOT_REFERENCE_DIR'])
    manifest = json.loads((root / 'uboot.json').read_text())
    for path in json.loads((root / 'references.json').read_text()):
      with self.subTest(path=path):
        nor = Path(path).read_bytes()
        offset, payload, _, digest = prepare(nor, manifest, lambda obj: (root / Path(urlparse(obj['url']).path).name).read_bytes())
        updated = nor[:offset] + payload + nor[offset + len(payload):]
        self.assertEqual(sha(updated), digest)
        self.assertFalse(prepare(updated, manifest, Mock(side_effect=AssertionError('Unexpected download')))[2])


if __name__ == '__main__':
  unittest.main()
