import importlib.machinery
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
password = importlib.machinery.SourceFileLoader('password', str(ROOT / 'userspace/root/usr/comma/persist-password')).load_module()


class PasswordTest(unittest.TestCase):
  def setUp(self):
    temp = tempfile.TemporaryDirectory()
    self.addCleanup(temp.cleanup)
    root = Path(temp.name)
    for name in ('SHADOW', 'SAVED', 'LOCK'):
      replacement = patch.object(password, name, root / name)
      replacement.start()
      self.addCleanup(replacement.stop)
    password.SHADOW.write_text('root:!:1:0:99999:7:::\ncomma:$6$first:1:0:99999:7:::\n')

  def test_password_survives_image_replacement_without_replacing_service_accounts(self):
    password.persist(restore=True)
    password.SHADOW.write_text('root:!:1:0:99999:7:::\ncomma:$6$changed:2:0:99999:7:::\n')
    password.persist()
    password.SHADOW.write_text('root:!:3:0:99999:7:::\nnewservice:!:3:0:99999:7:::\ncomma:$6$default:3:0:99999:7:::\n')
    password.persist(restore=True)
    self.assertIn('comma:$6$changed:2:', password.SHADOW.read_text())
    self.assertIn('newservice:!:3:', password.SHADOW.read_text())
    self.assertEqual(password.SAVED.stat().st_mode & 0o777, 0o600)
    self.assertEqual(password.SHADOW.stat().st_mode & 0o777, 0o600)

  def test_corruption_does_not_reset_password(self):
    password.SAVED.write_text('comma::2:0:99999:7:::\n')
    before = password.SHADOW.read_bytes()
    with self.assertRaises(ValueError):
      password.persist(restore=True)
    self.assertEqual(password.SHADOW.read_bytes(), before)

  def test_other_account_injection_rejected(self):
    password.SAVED.write_text('comma:$6$changed:2:0:99999:7:::\nroot:$6$bad:2:0:99999:7:::\n')
    with self.assertRaises(ValueError):
      password.persist(restore=True)


if __name__ == '__main__':
  unittest.main()
