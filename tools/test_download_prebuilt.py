import importlib.util
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("download_prebuilt", Path(__file__).parent / "build/download_prebuilt.py")
prebuilt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prebuilt)


class DownloadPrebuiltTests(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)
    self.source = self.root / "source"
    subprocess.run(["git", "init", "-q", "-b", "release-ci", str(self.source)], check=True)
    self.git("config", "user.name", "Release test")
    self.git("config", "user.email", "release-test@example.invalid")
    self.git("lfs", "install", "--local")
    self.git("lfs", "track", "*.bin")
    (self.source / "model.bin").write_bytes(bytes(range(256)) * 8)
    (self.source / "prebuilt").touch()
    self.metadata = {"channel": "release-ci", "openpilot": {"build_style": "prebuilt", "git_commit": "a" * 40}}
    (self.source / "build.json").write_text(json.dumps(self.metadata))
    self.git("add", ".")
    self.git("commit", "-qm", "Compiled release")
    self.output = self.root / "output/prebuilt.tar.xz"

  def git(self, *arguments):
    return subprocess.run(["git", "-C", str(self.source), *arguments], check=True)

  def test_roundtrip_keeps_compiled_branch_and_one_valid_lfs_copy(self):
    prebuilt.fetch_prebuilt(self.output, self.source.as_uri())
    restored = self.root / "restored"
    restored.mkdir()
    with tarfile.open(self.output) as archive:
      archive.extractall(restored, filter="fully_trusted")
    self.assertEqual(prebuilt.validate_checkout(restored), self.metadata)
    branch = subprocess.check_output(["git", "-C", str(restored), "branch", "--show-current"], text=True).strip()
    self.assertEqual(branch, "release-ci")
    oid = subprocess.check_output(["git", "-C", str(restored), "lfs", "ls-files", "--long"], text=True).split()[0]
    cached = restored / ".git/lfs/objects" / oid[:2] / oid[2:4] / oid
    self.assertTrue((restored / "model.bin").samefile(cached))

  def test_unbuilt_source_cannot_replace_existing_archive(self):
    self.metadata["channel"] = "release"
    (self.source / "build.json").write_text(json.dumps(self.metadata))
    self.git("add", "build.json")
    self.git("commit", "-qm", "Unbuilt source")
    self.output.parent.mkdir()
    self.output.write_bytes(b"previous-good-archive")
    with self.assertRaisesRegex(ValueError, "compiled release-ci"):
      prebuilt.fetch_prebuilt(self.output, self.source.as_uri())
    self.assertEqual(self.output.read_bytes(), b"previous-good-archive")


if __name__ == "__main__":
  unittest.main()
