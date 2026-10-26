#!/usr/bin/env python3
"""Flash published or local VamOS builds without the OS build toolchain."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(ROOT))

from tools.flash.devices import USB_ROOT, select_edl, show_devices
from tools.flash.releases import (
  LATEST_URL,
  UBOOT_URL,
  Downloads,
  check_version,
  factory_url,
  signed_json,
  validate_factory,
)


def parser():
  result = argparse.ArgumentParser(prog='vamos', description=__doc__)
  commands = result.add_subparsers(dest='command', required=True)
  commands.add_parser('devices', help='List Dragon devices and their USB link speed')
  command = commands.add_parser('flash', help='Flash a local build, or a published build with --remote')
  source = command.add_mutually_exclusive_group()
  source.add_argument('--remote', nargs='?', const='', type=lambda value: check_version(value) if value else '',
                      metavar='VERSION', help='Download the latest release, or the specified version')
  local_root = Path.cwd() if getattr(sys, 'frozen', False) else ROOT
  source.add_argument('--manifest', type=Path, default=local_root / 'build/flash-ufs/flash.json',
                      help='Local factory manifest (default: build/flash-ufs/flash.json)')
  command.add_argument('--no-openpilot', action='store_true', help='Install VamOS without openpilot')
  command.add_argument('--yes', action='store_true', help='Confirm installation without a prompt')
  command = commands.add_parser('uboot', help='Install published U-Boot; preserve the OS and data')
  command.add_argument('--yes', action='store_true', help='Confirm installation without a prompt')
  return result


def access_device(device):
  """Detach only the selected EDL interface, leaving other serial devices alone."""
  if select_edl() != device:
    raise ValueError('The connected device changed. Run the command again.')
  path = USB_ROOT / device.port
  bus, address = (int((path / name).read_text()) for name in ('busnum', 'devnum'))
  node = f'/dev/bus/usb/{bus:03d}/{address:03d}'
  interfaces = [interface for interface in USB_ROOT.glob(f'{device.port}:*')
                if (interface / 'driver').is_symlink() and (interface / 'driver').resolve().name == 'qcserial']
  if (interfaces or not os.access(node, os.R_OK | os.W_OK)) and os.geteuid() != 0:
    subprocess.run(['sudo', '-v'], check=True)
  for interface in interfaces:
    target = interface / 'driver/unbind'
    if os.geteuid() == 0:
      target.write_text(interface.name)
    else:
      subprocess.run(['sudo', '-n', 'tee', str(target)], input=interface.name, text=True, stdout=subprocess.DEVNULL, check=True)


def confirm(args, device):
  print(f'{device.product} | {device.serial} | {device.port} | {device.speed}')
  if args.command == 'uboot':
    print('Install U-Boot. The OS, recordings, settings, and identity are preserved.')
  else:
    print('Flashing VamOS replaces recordings and settings. Device identity is preserved.')
  if not args.yes and (not sys.stdin.isatty() or input('Continue? [y/N] ').strip().lower() not in ('y', 'yes')):
    raise ValueError('Installation cancelled. Use --yes for unattended flashing.')


def progress(index, total, phase):
  print(f'\r{phase.capitalize()} {index * 100 // total}%', end='', flush=True)


def install_factory(args, cache, downloads, device):
  from tools.flash.edl_normal import EdlDisk
  from tools.flash.flash_factory import flash_operations, prepare_operations

  include_openpilot = not args.no_openpilot
  if args.remote is None:
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text())
    validate_factory(manifest, include_openpilot)
  else:
    manifest_path = downloads.factory(cache / 'releases', args.remote or None, include_openpilot)
    manifest = json.loads(manifest_path.read_text())
  loader = manifest_path.parent / Path(urlparse(manifest['programmer']['url']).path).name
  obj = manifest['programmer']
  if loader.stat().st_size != obj['size'] or hashlib.sha256(loader.read_bytes()).hexdigest() != obj['sha256']:
    raise ValueError('Firehose programmer failed verification.')
  digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
  sector_size, operations = prepare_operations(manifest_path, cache / 'prepared' / digest, include_openpilot)
  print(f'Installing VamOS {manifest["version"]}', flush=True)
  access_device(device)
  with EdlDisk(str(loader), 'Ufs') as disk:
    flash_operations(disk, sector_size, operations, manifest['persist'], progress)
    disk.reset(1)
    print()
    return disk.write_bytes, disk.write_seconds


def install_uboot(cache, downloads, device):
  from tools.flash.edl_normal import EdlDisk, require
  from tools.flash.uboot_recipe import NOR_SIZE, prepare, sha, validate

  _, latest = signed_json(LATEST_URL)
  _, factory = signed_json(factory_url(check_version(latest.get('version'))))
  validate_factory(factory, False)
  loader = downloads.verified(factory['programmer'], cache / 'programmers')
  _, manifest = signed_json(UBOOT_URL)
  validate(manifest)
  access_device(device)
  with EdlDisk(str(loader), 'Spinor') as disk:
    require(disk.session.geometry['sectors'] * disk.sector_size == NOR_SIZE, 'Unsupported NOR size')
    print('Reading and verifying the NOR backup...', flush=True)
    nor = disk.read(0, NOR_SIZE)
    require(sha(disk.read(0, NOR_SIZE)) == sha(nor), 'NOR backup readback mismatch')
    backups = cache / 'nor-backups'
    backups.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = re.sub(r'[^a-zA-Z0-9-]', '_', device.serial)
    backup = backups / f'{name}-{sha(nor)}.bin'
    with tempfile.NamedTemporaryFile(dir=backups, delete=False) as output:
      temporary = Path(output.name)
      try:
        output.write(nor)
        output.flush()
        os.fsync(output.fileno())
        os.replace(temporary, backup)
        directory = os.open(backups, os.O_RDONLY | os.O_DIRECTORY)
        try:
          os.fsync(directory)
        finally:
          os.close(directory)
      finally:
        temporary.unlink(missing_ok=True)
    require(sha(backup.read_bytes()) == sha(nor), 'Saved NOR backup verification failed')
    offset, payload, changed, expected = prepare(nor, manifest, lambda obj: downloads.verified(obj, cache / 'uboot').read_bytes())
    if changed:
      print('Installing U-Boot...', flush=True)
      disk.write_verified(offset, payload)
    else:
      print('U-Boot is already current.', flush=True)
    require(sha(disk.read(0, NOR_SIZE)) == expected, 'Complete NOR verification failed')
    disk.reset(1)
    return disk.write_bytes, disk.write_seconds


def main(argv=None):
  args = parser().parse_args(argv)
  try:
    if sys.platform != 'linux' or os.uname().machine != 'x86_64':
      raise ValueError('The packaged USB backend currently supports Linux x86-64 only.')
    if args.command == 'devices':
      show_devices()
      return 0
    cache = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'vamos'
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (cache / 'flash.lock').open('w') as lock:
      fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
      device = select_edl()
      confirm(args, device)
      started = time.monotonic()
      downloads = Downloads()
      if args.command == 'uboot':
        count, seconds = install_uboot(cache, downloads, device)
      else:
        count, seconds = install_factory(args, cache, downloads, device)
      total = time.monotonic() - started
      download_speed = downloads.bytes / downloads.seconds / 1_000_000 if downloads.seconds else 0
      flash_speed = count / seconds / 1_000_000 if seconds else 0
      download_label = f'{download_speed:.1f} MB/s' if downloads.bytes else 'cached'
      flash_label = f'{flash_speed:.1f} MB/s' if count else 'unchanged'
      print(f'Complete | {total:.0f}s | Download {download_label} | Flash {flash_label} | {device.speed}')
      if args.command == 'uboot':
        print('Disconnect the PC cable and restart the device.')
      return 0
  except KeyboardInterrupt:
    print('\nInterrupted. Do not repeat an uncertain write automatically.', file=sys.stderr)
    return 130
  except (OSError, ValueError, RuntimeError, TimeoutError, KeyError, TypeError, subprocess.SubprocessError) as error:
    print(f'Error: {error}', file=sys.stderr)
    return 1


if __name__ == '__main__':
  raise SystemExit(main())
