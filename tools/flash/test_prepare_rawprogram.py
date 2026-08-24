from __future__ import annotations

import unittest

from prepare_rawprogram import operations_for_payload


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


if __name__ == "__main__":
  unittest.main()
