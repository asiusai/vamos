from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from package_flash import CHUNK_SIZE, package_image, package_programmer


class PackageFlashTest(unittest.TestCase):
  def test_packages_program_and_erase_chunks(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      image = root / "dragon.img"
      first = b"asius-v1" + bytes(CHUNK_SIZE - len("asius-v1"))
      image.write_bytes(first + bytes(CHUNK_SIZE))

      with patch("package_flash.OUTPUT_DIR", root / "out"):
        (root / "out").mkdir()
        chunks, digest, size = package_image(image, "https://updates.example/objects")

      self.assertEqual(size, CHUNK_SIZE * 2)
      self.assertEqual(digest, hashlib.sha256(first + bytes(CHUNK_SIZE)).hexdigest())
      self.assertEqual(chunks[0]["operation"], "program")
      self.assertLess(chunks[0]["compressed_size"], CHUNK_SIZE)
      self.assertEqual(chunks[1]["operation"], "erase")
      self.assertNotIn("url", chunks[1])

  def test_rejects_lfs_pointer_programmer(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      programmer = root / "programmer.elf"
      programmer.write_text("version https://git-lfs.github.com/spec/v1\n")
      with patch("package_flash.OUTPUT_DIR", root):
        with self.assertRaisesRegex(ValueError, "Git LFS pointer"):
          package_programmer(programmer, "https://updates.example/objects")


if __name__ == "__main__":
  unittest.main()
