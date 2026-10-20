import struct
import unittest
import zlib

from persist import initialize_persist


class PersistTest(unittest.TestCase):
  def disk(self, sector, with_persist=True):
    data = bytearray(2 * 1024 * 1024)
    persist = {'offset': 1024 * 1024, 'size': 65536}
    entries = bytearray(16384)
    if with_persist:
      pos = 5 * 128
      entries[pos:pos + 16] = b'1' * 16
      struct.pack_into('<QQ', entries, pos + 32, persist['offset'] // sector,
                       (persist['offset'] + persist['size']) // sector - 1)
      label = 'persist'.encode('utf-16-le')
      entries[pos + 56:pos + 56 + len(label)] = label
    data[2 * sector:2 * sector + len(entries)] = entries
    header = bytearray(sector)
    header[:8] = b'EFI PART'
    struct.pack_into('<II', header, 8, 0x10000, 92)
    struct.pack_into('<QIII', header, 72, 2, 128, 128, zlib.crc32(entries))
    struct.pack_into('<I', header, 16, zlib.crc32(header[:92]))
    data[sector:2 * sector] = header
    return data, persist

  def test_preserves_both_sector_sizes_and_corrupt_filesystems(self):
    for sector in (512, 4096):
      disk, persist = self.disk(sector)
      disk[persist['offset']:persist['offset'] + 10] = b'broken key'
      self.assertFalse(initialize_persist(lambda a, n: disk[a:a+n], sector, persist))

  def test_only_old_layout_can_initialize_the_region(self):
    disk, persist = self.disk(4096, False)
    read = lambda a, n: disk[a:a+n]
    self.assertTrue(initialize_persist(read, 4096, persist))
    start = persist['offset'] + 1024
    disk[start+56:start+58] = b'\x53\xef'
    disk[start+120:start+134] = b'VAMOS-IDENTITY\0'
    self.assertFalse(initialize_persist(read, 4096, persist))

  def test_interrupted_flash_does_not_erase_identity(self):
    disk, persist = self.disk(4096)
    disk[4096:8192] = bytes(4096)
    self.assertFalse(initialize_persist(lambda a, n: disk[a:a+n], 4096, persist))

  def test_moved_partition_and_damaged_gpt_stop_before_writing(self):
    disk, persist = self.disk(4096)
    with self.assertRaisesRegex(ValueError, 'geometry differs'):
      initialize_persist(lambda a, n: disk[a:a+n], 4096, dict(persist, offset=persist['offset']+4096))
    disk[4096+20] ^= 1
    with self.assertRaisesRegex(ValueError, 'checksum'):
      initialize_persist(lambda a, n: disk[a:a+n], 4096, persist)
