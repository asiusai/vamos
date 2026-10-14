import os
from pathlib import Path
from unittest.mock import patch
from tools.ssh_key import default_ssh_key


def test_operator_key_override():
  with patch.dict(os.environ, {"DRAGON_SSH_KEY": "~/operator-key"}):
    assert default_ssh_key() == Path("~/operator-key").expanduser()


def test_no_shared_bootstrap_key():
  with patch.dict(os.environ, {}, clear=True):
    assert default_ssh_key().name == 'asius_operator_ed25519'
  assert not (Path(__file__).parent / 'ssh/comma_setup.b64').exists()
