from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.build.archive_ota import archive_update


class ArchiveOtaTest(unittest.TestCase):
  def test_splits_images_and_writes_reconstructable_manifest(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      input_dir = root / "ota"
      output_dir = root / "archive"
      input_dir.mkdir()
      images = {
        "esp": b"abcd",
        "system": b"0123456789abcdef",
      }
      partitions = []
      for name, payload in images.items():
        filename = f"{name}-hash.img.xz"
        (input_dir / filename).write_bytes(payload)
        partitions.append({
          "name": name,
          "url": f"https://updates.example/objects/{filename}",
          "compressed_sha256": hashlib.sha256(payload).hexdigest(),
        })
      (input_dir / "vamos.json").write_text(json.dumps({
        "product": "asius-v1",
        "version": "test-1",
        "partitions": partitions,
      }))
      (input_dir / "vamos.json.sig").write_bytes(b"s" * 64)

      archive_update(input_dir, output_dir, "a" * 40, chunk_size=5)

      release = json.loads((output_dir / "release.json").read_text())
      archive = json.loads((output_dir / "manifest.json").read_text())
      self.assertEqual(release["source_commit"], "a" * 40)
      self.assertEqual(release["device_manifest"], "vamos.json")
      for partition in archive:
        if partition["url"]:
          reconstructed = (output_dir / Path(partition["url"]).name).read_bytes()
        else:
          reconstructed = b"".join(
            (output_dir / Path(chunk["url"]).name).read_bytes()
            for chunk in partition["chunks"]
          )
        self.assertEqual(reconstructed, images[partition["name"]])
        self.assertTrue(all(chunk["size"] <= 5 for chunk in partition.get("chunks", [])))
      self.assertTrue(archive[0]["url"].endswith("esp-hash.img.xz"))
      self.assertEqual(archive[1]["url"], "")

  def test_rejects_invalid_signature(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      input_dir = Path(temporary) / "ota"
      input_dir.mkdir()
      (input_dir / "vamos.json").write_text('{"partitions": []}')
      (input_dir / "vamos.json.sig").write_bytes(b"short")
      with self.assertRaisesRegex(ValueError, "64-byte"):
        archive_update(input_dir, Path(temporary) / "archive", "commit")


if __name__ == "__main__":
  unittest.main()
