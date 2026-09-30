"""Check boot hostname normalization without changing the test host's name."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'userspace/root/usr/comma/set-hostname.sh'


class DeviceNameTest(unittest.TestCase):
  def test_hostname_from_param(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      (root / 'cat').write_text('#!/bin/sh\nprintf "%s" "$TEST_DEVICE_NAME"\n')
      (root / 'hostname').write_text('#!/bin/sh\nprintf "%s" "$1" > "$TEST_HOSTNAME_OUTPUT"\n')
      for command in ('cat', 'hostname'):
        (root / command).chmod(0o755)
      output = root / 'hostname-output'
      cases = [('Asius v0 2', 'asius-v0-2'), ('  My Car!! ', 'my-car'),
               ('$(touch /tmp/nope); CAR', 'touch-tmp-nope-car'), ('My\nCar', 'my-car'),
               ('🚗', 'asius-v0'), ('', 'asius-v0'), ('a' * 62 + ' - tail', 'a' * 62)]
      for name, expected in cases:
        with self.subTest(name=name):
          env = {**os.environ, 'PATH': f'{root}:{os.environ["PATH"]}',
                 'TEST_DEVICE_NAME': name, 'TEST_HOSTNAME_OUTPUT': str(output)}
          subprocess.run(['sh', str(SCRIPT)], env=env, check=True, capture_output=True)
          self.assertEqual(output.read_text(), expected)


if __name__ == '__main__':
  unittest.main()
