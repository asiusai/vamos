"""Apply the manual default USB/UART password setting."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

PARAM = Path('/data/params/d/PhysicalPasswordLogin')
POLICY = Path('/run/vamos-physical-ssh.conf')
UART_ENABLED = Path('/run/vamos-physical-login-enabled')


def enabled() -> bool:
  try:
    with PARAM.open('rb') as stream:
      return stream.read(2) == b'1'
  except FileNotFoundError:
    return True


def apply() -> bool:
  allow = enabled()
  policy = f'PasswordAuthentication {"yes" if allow else "no"}\n'.encode()
  if POLICY.exists() and POLICY.read_bytes() == policy and UART_ENABLED.exists() == allow:
    return False
  with tempfile.NamedTemporaryFile(dir=POLICY.parent, prefix='.vamos-physical-', delete=False) as stream:
    temporary = Path(stream.name)
    stream.write(policy)
    stream.flush()
    os.fchmod(stream.fileno(), 0o644)
  try:
    os.replace(temporary, POLICY)
  finally:
    temporary.unlink(missing_ok=True)
  if allow:
    UART_ENABLED.touch(mode=0o600)
  else:
    UART_ENABLED.unlink(missing_ok=True)
  return True


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--reload', action='store_true')
  arguments = parser.parse_args()
  if os.geteuid() != 0:
    raise SystemExit('Root is required to apply the login policy')
  if apply() and arguments.reload:
    for service, action in (('sshd', 'hup'), ('agetty-ttyMSM0', 'restart'), ('agetty-ttyAMA0', 'restart')):
      path = Path('/var/service') / service
      if path.exists():
        subprocess.run(['sv', action, str(path)], check=True, timeout=15)
