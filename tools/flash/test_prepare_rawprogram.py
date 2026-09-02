from __future__ import annotations

import hashlib
import json
import lzma
import tempfile
import unittest
from pathlib import Path

from prepare_rawprogram import operations_for_payload, prepare_payload


class PrepareRawprogramTest(unittest.TestCase):
  def test_reads_legacy_bundled_operations_as_base(self) -> None:
    operations = [{"operation": "erase", "offset": 0, "size": 512}]
    manifest = {"manifest_version": 1, "sector_size": 512, "chunks": operations}
    self.assertIs(operations_for_payload(manifest, "base"), operations)

  def test_rejects_a_separate_legacy_openpilot_payload(self) -> None:
    manifest = {"manifest_version": 1, "sector_size": 512, "chunks": []}
    with self.assertRaisesRegex(ValueError, "bundled payload"):
      operations_for_payload(manifest, "openpilot")

  def test_reads_current_payloads(self) -> None:
    base = [{"operation": "erase", "offset": 0, "size": 512}]
    openpilot = [{"operation": "program", "offset": 512, "size": 512}]
    manifest = {
      "manifest_version": 2,
      "sector_size": 512,
      "base": {"operations": base},
      "optional_payloads": {"openpilot": {"operations": openpilot}},
    }
    self.assertIs(operations_for_payload(manifest, "base"), base)
    self.assertIs(operations_for_payload(manifest, "openpilot"), openpilot)

  def test_accepts_ufs_sector_size(self) -> None:
    operations = [{"operation": "erase", "offset": 0, "size": 4096}]
    manifest = {
      "manifest_version": 2,
      "sector_size": 4096,
      "base": {"operations": operations},
    }
    self.assertIs(operations_for_payload(manifest, "base"), operations)

  def test_rejects_unaligned_ufs_operation(self) -> None:
    manifest = {
      "manifest_version": 2,
      "sector_size": 4096,
      "base": {"operations": [{"operation": "program", "offset": 512, "size": 4096}]},
    }
    with self.assertRaisesRegex(ValueError, "aligned"):
      operations_for_payload(manifest, "base")

  def test_reuses_verified_prepared_payload_and_rebuilds_changed_output(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      raw = b"asius-v0" + bytes(512 - len("asius-v0"))
      compressed = lzma.compress(raw)
      source = root / "object.img.xz"
      source.write_bytes(compressed)
      manifest_path = root / "flash.json"
      manifest_path.write_text(json.dumps({
        "manifest_version": 2,
        "sector_size": 512,
        "base": {"operations": [{
          "compressed_sha256": hashlib.sha256(compressed).hexdigest(),
          "compressed_size": len(compressed),
          "offset": 512,
          "operation": "program",
          "sha256": hashlib.sha256(raw).hexdigest(),
          "size": len(raw),
          "url": f"https://updates.example/{source.name}",
        }]},
      }))
      output = root / "prepared"

      self.assertFalse(prepare_payload(manifest_path, output, "base"))
      self.assertTrue(prepare_payload(manifest_path, output, "base"))
      manifest = json.loads(manifest_path.read_text())
      manifest["unrelated_release_metadata"] = "changed"
      manifest_path.write_text(json.dumps(manifest))
      self.assertTrue(prepare_payload(manifest_path, output, "base"))
      chunk = output / "chunk-00000.img"
      chunk.write_bytes(bytes(len(raw)))
      self.assertFalse(prepare_payload(manifest_path, output, "base"))
      self.assertEqual(chunk.read_bytes(), raw)


if __name__ == "__main__":
  unittest.main()
