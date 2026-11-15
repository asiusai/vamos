import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('physical_login', Path(__file__).parents[1] / 'userspace/root/usr/lib/vamos/physical_login.py')
login = importlib.util.module_from_spec(spec)
spec.loader.exec_module(login)


class PhysicalLoginTest(unittest.TestCase):
  def test_default_and_atomic_parameter_changes(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      with patch.object(login, 'PARAM', root / 'param'), patch.object(login, 'POLICY', root / 'policy'), patch.object(login, 'UART_ENABLED', root / 'uart'):
        self.assertTrue(login.apply())
        self.assertEqual(login.POLICY.read_text(), 'PasswordAuthentication yes\n')
        self.assertTrue(login.UART_ENABLED.exists())
        self.assertFalse(login.apply())
        replacement = root / 'new'
        replacement.write_bytes(b'0')
        replacement.replace(login.PARAM)
        self.assertTrue(login.apply())
        self.assertEqual(login.POLICY.read_text(), 'PasswordAuthentication no\n')
        self.assertFalse(login.UART_ENABLED.exists())
        replacement.write_bytes(b'1')
        replacement.replace(login.PARAM)
        self.assertTrue(login.apply())
        self.assertTrue(login.UART_ENABLED.exists())
        login.PARAM.unlink()
        self.assertFalse(login.apply())

  def test_malformed_existing_value_does_not_enable_passwords(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / 'param'
      with patch.object(login, 'PARAM', path):
        for value in (b'', b'yes', b'10', b'1\n', b'\x00'):
          with self.subTest(value=value):
            path.write_bytes(value)
            self.assertFalse(login.enabled())


if __name__ == '__main__':
  unittest.main()
