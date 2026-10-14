"""Operator SSH identity for VamOS host tools. No shared bootstrap credential."""
import os
from pathlib import Path


def default_ssh_key() -> Path:
  return Path(os.environ.get("DRAGON_SSH_KEY", "~/.ssh/asius_operator_ed25519")).expanduser()
