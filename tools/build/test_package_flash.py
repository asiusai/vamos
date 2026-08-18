from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from package_flash import CHUNK_SIZE, package_base_image, package_delta, package_programmer


class PackageFlashTest(unittest.TestCase):
  def test_base_erases_target_and_packages_only_nonzero_chunks(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      image = root / "dragon.img"
      first = b"asius-v1" + bytes(CHUNK_SIZE - len("asius-v1"))
      image.write_bytes(first + bytes(CHUNK_SIZE))

      with patch("package_flash.OUTPUT_DIR", root / "out"):
        (root / "out").mkdir()
        operations, size = package_base_image(image, "https://updates.example/objects")

      self.assertEqual(size, CHUNK_SIZE * 2)
      self.assertEqual(operations[0], {"offset": 0, "operation": "erase", "size": size})
      self.assertEqual(len(operations), 2)
      self.assertEqual(operations[1]["operation"], "program")
      self.assertLess(operations[1]["compressed_size"], CHUNK_SIZE)

  def test_delta_packages_program_and_erase_ranges(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      baseline = root / "userdata.img"
      updated = root / "userdata-openpilot.img"
      keep = b"same" + bytes(CHUNK_SIZE - len("same"))
      removed = b"remove" + bytes(CHUNK_SIZE - len("remove"))
      added = b"openpilot" + bytes(CHUNK_SIZE - len("openpilot"))
      baseline.write_bytes(keep + removed + bytes(CHUNK_SIZE))
      updated.write_bytes(keep + bytes(CHUNK_SIZE) + added)

      with patch("package_flash.OUTPUT_DIR", root / "out"):
        (root / "out").mkdir()
        operations, size = package_delta(
          baseline,
          updated,
          4096,
          "https://updates.example/objects",
        )

      self.assertEqual(size, CHUNK_SIZE * 3)
      self.assertEqual(len(operations), 2)
      self.assertEqual(operations[0], {
        "offset": 4096 + CHUNK_SIZE,
        "operation": "erase",
        "size": CHUNK_SIZE,
      })
      self.assertEqual(operations[1]["offset"], 4096 + 2 * CHUNK_SIZE)
      self.assertEqual(operations[1]["operation"], "program")

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
