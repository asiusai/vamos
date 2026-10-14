"""Persistent clock floor and forward-only runtime synchronization."""
import argparse
import contextlib
import ctypes
import fcntl
import json
import math
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

STATE = Path('/data/vamos-clock/state.json')
RUNTIME = Path('/run/vamos-clock.json')
SYNCED = Path('/run/vamos-time-synced')
BUILD = Path('/etc/vamos-build-time')
BOOT_ID = Path('/proc/sys/kernel/random/boot_id')
TEN_YEARS = 315576000


def read_json(path):
  try:
    value = json.loads(path.read_text())
    return value if isinstance(value, dict) else {}
  except (OSError, ValueError):
    return {}


def atomic_json(path, value):
  path.parent.mkdir(parents=True, exist_ok=True)
  temp = path.with_suffix('.tmp')
  with temp.open('w') as f:
    json.dump(value, f)
    f.flush()
    os.fsync(f.fileno())
  os.replace(temp, path)
  fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
  try:
    os.fsync(fd)
  finally:
    os.close(fd)


@contextlib.contextmanager
def locked():
  with open('/run/vamos-clock.lock', 'w') as f:
    fcntl.flock(f, fcntl.LOCK_EX)
    yield


def valid_epoch(value, build):
  return isinstance(value, (float, int)) and math.isfinite(value) and build <= value <= build + TEN_YEARS


def boot_floor(build, saved):
  """Recover without trusting any clock value supplied by the board."""
  return max(value for value in (build, saved) if valid_epoch(value, build))


def slew(seconds):
  # adjtime slows/speeds CLOCK_REALTIME without ever stepping it backwards.
  # Bound each adjustment for libc's timeval range; subsequent polls continue it.
  class Timeval(ctypes.Structure):
    _fields_ = [('tv_sec', ctypes.c_long), ('tv_usec', ctypes.c_long)]
  delta = max(-2000.0, min(2000.0, seconds))
  whole = math.trunc(delta)
  value = Timeval(whole, math.trunc((delta - whole) * 1_000_000))
  libc = ctypes.CDLL(None, use_errno=True)
  if libc.adjtime(ctypes.byref(value), None) != 0:
    error = ctypes.get_errno()
    raise OSError(error, os.strerror(error))


def checkpoint():
  build = int(BUILD.read_text())
  now = time.time()
  state = read_json(STATE)
  saved = state.get('unixTime', build)
  if not valid_epoch(now, build):
    raise ValueError('Refusing to checkpoint an implausible clock')
  atomic_json(STATE, {'unixTime': max(now, saved) if valid_epoch(saved, build) else now})


def restore():
  # Only stage 1 calls restore. Never let a service restart reset a live clock.
  boot = BOOT_ID.read_text().strip()
  if read_json(RUNTIME).get('bootId') == boot:
    return
  build = int(BUILD.read_text())
  now = time.time()
  # No RTC is available for retention on Asius v0. Even a plausible hardware
  # date is untrusted; the checkpoint is the only saved clock across power loss.
  target = boot_floor(build, read_json(STATE).get('unixTime'))
  if target != now:
    time.clock_settime(time.CLOCK_REALTIME, target)
  atomic_json(RUNTIME, {'bootId': boot})
  checkpoint()
  print(f'clock: restored {target:.3f}', flush=True)


def synchronize(target, source):
  build = int(BUILD.read_text())
  if not valid_epoch(target, build):
    raise ValueError('Time sample is outside the image lifetime')
  runtime = read_json(RUNTIME)
  mono = time.monotonic()
  # A phone with an incorrect clock must not override a recent NTP fix.
  if source == 'app' and runtime.get('source') == 'nts' and 0 <= mono - runtime.get('monotonic', -1000) < 600:
    return {'accepted': False, 'correction': 'network-clock-current'}
  offset = target - time.time()
  if offset > 0.25:
    slew(0)
    time.clock_settime(time.CLOCK_REALTIME, target)
    correction = 'forward'
  else:
    slew(offset)
    correction = 'slew'
  runtime.update(source=source, monotonic=mono, offset=offset, correction=correction)
  atomic_json(RUNTIME, runtime)
  checkpoint()
  # Tailscale may now attempt TLS even when a small negative correction is pending.
  SYNCED.touch()
  print(f'clock: {source} {correction} {offset:+.3f}s', file=sys.stderr, flush=True)
  return {'accepted': True, 'correction': correction}


def parse_chrony_tracking(text, now):
  # chronyc CSV: reference ID, address, stratum, reference UTC, correction, ...,
  # root delay, root dispersion, update interval, leap status.
  fields = text.strip().split(',')
  if len(fields) != 14 or fields[13] != 'Normal' or fields[0] == '00000000':
    raise ValueError('Authenticated time is not synchronized')
  stratum = int(fields[2])
  reference, correction = float(fields[3]), float(fields[4])
  delay, dispersion = float(fields[10]), float(fields[11])
  target = now - correction
  if not all(math.isfinite(value) for value in (reference, correction, delay, dispersion)):
    raise ValueError('Invalid authenticated time sample')
  if not 1 <= stratum <= 15 or not 0 <= target - reference <= 180 or not 0 <= delay / 2 + dispersion <= 2:
    raise ValueError('Stale or unreliable authenticated time sample')
  return target


def query_nts():
  # Chrony measures time without changing CLOCK_REALTIME. This helper retains
  # the existing forward-step/backward-slew policy for both NTS and app samples.
  result = subprocess.run(['chronyc', '-h', '/run/chrony/chronyd.sock', '-c', 'tracking'], capture_output=True, text=True, timeout=3, check=True)
  return parse_chrony_tracking(result.stdout, time.time())


def serve():
  stopping = False
  def stop(*_):
    nonlocal stopping
    stopping = True
  signal.signal(signal.SIGTERM, stop)
  signal.signal(signal.SIGINT, stop)
  next_poll = next_save = 0.0
  while not stopping:
    now = time.monotonic()
    if now >= next_poll:
      success = False
      try:
        target = query_nts()
        with locked():
          synchronize(target, 'nts')
        success = True
      except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'clock: NTS not ready: {type(error).__name__}', flush=True)
      next_poll = time.monotonic() + (15 if success else 2)
    if now >= next_save:
      try:
        with locked():
          checkpoint()
      except (OSError, ValueError) as error:
        print(f'clock: checkpoint: {error}', flush=True)
      next_save = time.monotonic() + 30
    time.sleep(1)
  with locked():
    checkpoint()


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest='command', required=True)
  commands.add_parser('restore')
  commands.add_parser('serve')
  commands.add_parser('checkpoint')
  commands.add_parser('status')
  sync = commands.add_parser('sync')
  sync.add_argument('--unix-ms', required=True, type=float)
  sync.add_argument('--source', choices=('app',), required=True)
  args = parser.parse_args()
  if args.command == 'serve':
    serve()
  else:
    with locked():
      if args.command == 'restore':
        restore()
      elif args.command == 'checkpoint':
        checkpoint()
      elif args.command == 'status':
        print(json.dumps({'unixTime': time.time(), 'saved': read_json(STATE), 'sync': read_json(RUNTIME)}))
      else:
        print(json.dumps(synchronize(args.unix_ms / 1000, args.source)))
  return 0
