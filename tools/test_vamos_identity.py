import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

spec = importlib.util.spec_from_file_location('vamos_identity', Path(__file__).resolve().parents[1] / 'userspace/root/usr/lib/vamos/identity.py')
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)


@pytest.fixture
def volume(tmp_path, monkeypatch):
  monkeypatch.setattr(identity, 'KEY', tmp_path / 'asius/id_ed25519')
  monkeypatch.setattr(identity, 'MARKER', tmp_path / '.provisioned')
  # Tests run as an ordinary user; production runs as root.
  monkeypatch.setattr(identity.os, 'chown', lambda *args: None)
  original_fchown = os.fchown
  monkeypatch.setattr(identity.os, 'fchown', lambda fd, uid, gid: original_fchown(fd, os.getuid(), os.getgid()))
  return tmp_path


def test_first_boot_and_repeat_preserve_the_identity(volume):
  first = identity.provision(os.getuid(), os.getgid())
  original = identity.KEY.read_bytes()
  second = identity.provision(os.getuid(), os.getgid())
  assert first.private_bytes_raw() == second.private_bytes_raw()
  assert identity.KEY.read_bytes() == original
  assert identity.KEY.stat().st_mode & 0o777 == 0o600


def test_missing_and_corrupt_identity_do_not_generate_replacements(volume):
  identity.provision(os.getuid(), os.getgid())
  identity.KEY.write_text('corrupt')
  with pytest.raises(ValueError):
    identity.provision(os.getuid(), os.getgid())
  identity.KEY.unlink()
  with pytest.raises(ValueError, match='missing'):
    identity.provision(os.getuid(), os.getgid())
  assert not identity.KEY.exists()


def test_entropy_failure_and_interrupted_provisioning(volume):
  with patch.object(identity, 'secure_key', side_effect=OSError('no entropy')):
    with pytest.raises(OSError):
      identity.provision(os.getuid(), os.getgid())
  assert not identity.KEY.exists()
  first = identity.provision(os.getuid(), os.getgid())
  identity.MARKER.unlink()
  assert identity.provision(os.getuid(), os.getgid()).private_bytes_raw() == first.private_bytes_raw()


def test_explicit_replacement_and_import(volume):
  first = identity.provision(os.getuid(), os.getgid())
  replacement = Ed25519PrivateKey.generate()
  identity.provision(os.getuid(), os.getgid(), replacement)
  assert identity.load_key(identity.KEY).private_bytes_raw() == replacement.private_bytes_raw()
  assert first.private_bytes_raw() != replacement.private_bytes_raw()


def test_symlink_is_rejected(volume):
  identity.KEY.parent.mkdir()
  identity.KEY.symlink_to(volume / 'other')
  with pytest.raises(ValueError, match='symlinks'):
    identity.provision(os.getuid(), os.getgid())
