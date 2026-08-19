"""Shared bootstrap SSH identity used by vamOS host tools."""

from __future__ import annotations

import base64
import os
from pathlib import Path


SHARED_KEY = Path(__file__).resolve().parent / "ssh" / "comma_setup.b64"


def shared_key_bytes() -> bytes:
  return base64.b64decode(SHARED_KEY.read_bytes().strip(), validate=True)


def default_ssh_key() -> Path:
  """Return an OpenSSH-safe copy of the intentionally shared setup key."""
  configured = os.environ.get("DRAGON_SSH_KEY")
  if configured:
    return Path(configured).expanduser()

  cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
  cache_dir = cache_root / "vamos"
  cached_key = cache_dir / "comma_setup"
  cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
  cache_dir.chmod(0o700)

  key_bytes = shared_key_bytes()
  if not cached_key.exists() or cached_key.read_bytes() != key_bytes:
    temporary = cache_dir / "comma_setup.tmp"
    temporary.write_bytes(key_bytes)
    temporary.chmod(0o600)
    temporary.replace(cached_key)
  cached_key.chmod(0o600)
  return cached_key
