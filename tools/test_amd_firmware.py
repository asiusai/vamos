import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

import zstandard

SPEC = importlib.util.spec_from_file_location("amd_firmware", Path(__file__).resolve().parents[1] / "userspace/install_amd_firmware.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AMDFirmwareTest(unittest.TestCase):
  def test_installs_verified_compressed_firmware(self):
    with tempfile.TemporaryDirectory() as directory:
      source = Path(directory) / "source"
      source.mkdir()
      data = b"firmware payload"
      (source / "psp.bin").write_bytes(data)
      root = Path(directory) / "root"
      MODULE.install(root, {"psp.bin": hashlib.sha256(data).hexdigest()}, source)
      installed = root / "lib/firmware/amdgpu/psp.bin.zst"
      self.assertEqual(zstandard.decompress(installed.read_bytes()), data)
      self.assertEqual(installed.stat().st_mode & 0o777, 0o644)

  def test_incomplete_or_corrupt_set_preserves_installed_files(self):
    for bad_data in (None, b"corrupt"):
      with self.subTest(bad_data=bad_data), tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "source"
        source.mkdir()
        (source / "psp.bin").write_bytes(b"replacement")
        if bad_data is not None:
          (source / "smu.bin").write_bytes(bad_data)
        root = Path(directory) / "root"
        installed = root / "lib/firmware/amdgpu/psp.bin.zst"
        installed.parent.mkdir(parents=True)
        installed.write_bytes(b"original")
        hashes = {"psp.bin": hashlib.sha256(b"replacement").hexdigest(), "smu.bin": hashlib.sha256(b"expected").hexdigest()}
        with self.assertRaises((FileNotFoundError, ValueError)):
          MODULE.install(root, hashes, source)
        self.assertEqual(installed.read_bytes(), b"original")
        self.assertEqual(list(installed.parent.iterdir()), [installed])


if __name__ == "__main__":
  unittest.main()
