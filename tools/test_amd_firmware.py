import hashlib
import io
import tarfile
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import zstandard

from userspace import install_amd_firmware as firmware


def archive_bytes(files):
  output = io.BytesIO()
  with tarfile.open(fileobj=output, mode="w:gz") as archive:
    for name, data in files:
      member = tarfile.TarInfo(name)
      member.size = len(data)
      archive.addfile(member, io.BytesIO(data))
  return output.getvalue()


class FirmwareTest(unittest.TestCase):
  def test_rate_limit_retries_same_pinned_url(self):
    error = urllib.error.HTTPError("firmware", 429, "rate limited", {"Retry-After": "3"}, None)
    responses = [error, io.BytesIO(b"firmware")]
    with (patch.object(firmware.urllib.request, "urlopen", side_effect=responses) as request,
          patch.object(firmware.time, "sleep") as sleep):
      self.assertEqual(firmware.download(), b"firmware")
    sleep.assert_called_once_with(3)
    self.assertEqual(request.call_args_list[0], request.call_args_list[1])
    self.assertIn(firmware.FIRMWARE_REVISION, request.call_args.args[0])

  def test_permanent_error_is_not_retried(self):
    error = urllib.error.HTTPError("firmware", 404, "missing", {}, None)
    with (patch.object(firmware.urllib.request, "urlopen", side_effect=error) as request,
          patch.object(firmware.time, "sleep") as sleep, self.assertRaises(urllib.error.HTTPError)):
      firmware.download()
    request.assert_called_once()
    sleep.assert_not_called()

  def test_transient_failure_has_bounded_retries(self):
    error = urllib.error.HTTPError("firmware", 503, "unavailable", {}, None)
    with (patch.object(firmware.urllib.request, "urlopen", side_effect=error) as request,
          patch.object(firmware.time, "sleep") as sleep, self.assertRaises(urllib.error.HTTPError)):
      firmware.download()
    self.assertEqual(request.call_count, 6)
    self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4, 8, 16])

  def test_bad_hash_leaves_installed_firmware_unchanged(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      target = root / "lib/firmware/amdgpu/test.bin.zst"
      target.parent.mkdir(parents=True)
      target.write_bytes(b"previous firmware")
      data = archive_bytes([("test.bin", b"incorrect")])
      with patch.object(firmware, "download", return_value=data), self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
        firmware.install(root, {"test.bin": hashlib.sha256(b"correct").hexdigest()})
      self.assertEqual(target.read_bytes(), b"previous firmware")

  def test_archive_installs_only_verified_requested_files(self):
    data = archive_bytes([("test.bin", b"correct"), ("../../unwanted", b"ignored")])
    with tempfile.TemporaryDirectory() as directory, patch.object(firmware, "download", return_value=data) as request:
      root = Path(directory)
      firmware.install(root, {"test.bin": hashlib.sha256(b"correct").hexdigest()})
      installed = list(root.rglob("*"))
      self.assertEqual([path.name for path in installed if path.is_file()], ["test.bin.zst"])
      self.assertEqual(zstandard.decompress(installed[-1].read_bytes()), b"correct")
      request.assert_called_once_with()

  def test_incomplete_or_duplicate_archive_does_not_install(self):
    for files in ([], [("test.bin", b"correct"), ("test.bin", b"correct")]):
      with self.subTest(files=files), tempfile.TemporaryDirectory() as directory:
        with patch.object(firmware, "download", return_value=archive_bytes(files)), self.assertRaises(ValueError):
          firmware.install(Path(directory), {"test.bin": hashlib.sha256(b"correct").hexdigest()})
        self.assertEqual(list(Path(directory).iterdir()), [])


class AMDFirmwareTest(unittest.TestCase):
  def test_installs_verified_compressed_firmware(self):
    with tempfile.TemporaryDirectory() as directory:
      source = Path(directory) / "source"
      source.mkdir()
      data = b"firmware payload"
      (source / "psp.bin").write_bytes(data)
      root = Path(directory) / "root"
      firmware.install(root, {"psp.bin": hashlib.sha256(data).hexdigest()}, source)
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
          firmware.install(root, hashes, source)
        self.assertEqual(installed.read_bytes(), b"original")
        self.assertEqual(list(installed.parent.iterdir()), [installed])


if __name__ == "__main__":
  unittest.main()
