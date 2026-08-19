from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.ssh_key import SHARED_KEY, default_ssh_key, shared_key_bytes


class SharedSshKeyTest(unittest.TestCase):
  def test_default_key_is_materialized_with_safe_permissions(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      with patch.dict(
        os.environ,
        {"XDG_CACHE_HOME": temporary},
        clear=False,
      ):
        os.environ.pop("DRAGON_SSH_KEY", None)
        key = default_ssh_key()

      self.assertEqual(key.read_bytes(), shared_key_bytes())
      self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
      public = subprocess.check_output(
        ["ssh-keygen", "-y", "-f", str(key)], text=True
      ).strip()
      expected = SHARED_KEY.with_suffix(".pub").read_text()
      self.assertEqual(public.split()[:2], expected.split()[:2])

  def test_environment_override_is_unchanged(self) -> None:
    configured = Path("~/operator-key").expanduser()
    with patch.dict(os.environ, {"DRAGON_SSH_KEY": "~/operator-key"}):
      self.assertEqual(default_ssh_key(), configured)


if __name__ == "__main__":
  unittest.main()
