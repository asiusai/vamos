"""Exercise boot-time recovery without changing the host clock or resolver."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "userspace/root/etc/sv/busybox-ntpd/run"
BUILD_TIME = 1788710400

MOCK = '''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ["TIME_TEST_ROOT"])
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with (root / "calls").open("a") as f:
 f.write(json.dumps([name, args]) + "\\n")
if name == "date" and args == ["+%s"]:
 print(os.environ["TIME_TEST_NOW"])
if name == "busybox":
 assert args[0] == "ntpd"
 assert args[2:] == ["-p", "162.159.200.1", "-p", "162.159.200.123"]
 marker = root / "synced"
 if args[1] == "-nqN":
  assert not marker.exists(), "must not signal success before NTP sync"
  attempt = root / "attempt"
  if os.environ.get("TIME_TEST_RETRY") and not attempt.exists():
   attempt.touch()
   sys.exit(1)
 else:
  assert args[1] == "-nN" and marker.exists()
'''


class TimeSyncTest(unittest.TestCase):
  def run_service(self, now, retry=False, build_time=str(BUILD_TIME)):
    with tempfile.TemporaryDirectory() as temp:
      root = Path(temp)
      (root / "build-time").write_text(build_time)
      (root / "rtc").touch()
      (root / "synced").touch()  # a service restart must clear stale success
      for name in ["date", "busybox", "sleep", "hwclock"]:
        p = root / name
        p.write_text(MOCK)
        p.chmod(0o755)
      script = RUN.read_text().replace('/etc/vamos-build-time', str(root / 'build-time'))
      script = script.replace('/run/vamos-time-synced', str(root / 'synced'))
      script = script.replace('/dev/rtc0', str(root / 'rtc'))
      env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}", TIME_TEST_ROOT=temp,
                 TIME_TEST_NOW=str(now), TIME_TEST_RETRY="1" if retry else "")
      result = subprocess.run(['sh', '-c', script], env=env, capture_output=True, text=True, timeout=5)
      calls = [json.loads(s) for s in (root / 'calls').read_text().splitlines()] if (root / 'calls').exists() else []
      return result, calls

  def test_future_rtc_is_seeded_before_ntp(self):
    result, calls = self.run_service(5762161462)
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertEqual(calls[1], ['date', ['-u', '-s', f'@{BUILD_TIME}']])
    self.assertTrue(any(name == 'hwclock' and args[0] == '--rtc' and
                        args[2:] == ['--systohc', '--utc'] for name, args in calls))

  def test_missing_rtc_date_is_seeded(self):
    result, calls = self.run_service(0)
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertEqual(calls[1], ['date', ['-u', '-s', f'@{BUILD_TIME}']])

  def test_plausible_clock_is_not_reset(self):
    result, calls = self.run_service(BUILD_TIME + 86400)
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertEqual(sum(name == 'date' for name, _ in calls), 1)

  def test_failed_ntp_retries_before_marking_synchronized(self):
    result, calls = self.run_service(BUILD_TIME, retry=True)
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertIn(['sleep', ['10']], calls)
    self.assertEqual(sum(name == 'busybox' for name, _ in calls), 3)

  def test_invalid_build_time_fails_without_touching_clock(self):
    result, calls = self.run_service(BUILD_TIME, build_time='invalid')
    self.assertNotEqual(result.returncode, 0)
    self.assertEqual(calls, [])


if __name__ == '__main__':
  unittest.main()
