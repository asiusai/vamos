"""Download the same signed factory releases as flash.asius.ai."""

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from tools.flash.prepare_rawprogram import operations_for_payload

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_KEY = ROOT / 'userspace/root/usr/share/vamos/update-public.pem'
LATEST_URL = 'https://dist.asius.ai/openpilot/vamos.json'
UBOOT_URL = 'https://dist.asius.ai/bootloader/uboot.json'
VERSION = re.compile(r'\d+\.\d+(?:\.\d+)?')
HASH = re.compile(r'[a-f0-9]{64}')


def check_version(version: str) -> str:
  if not isinstance(version, str) or VERSION.fullmatch(version) is None:
    raise ValueError('Release must be a version such as 18.11.')
  return version


def factory_url(version: str) -> str:
  return f'https://dist.asius.ai/vamos/releases/{check_version(version)}/flash-ufs.json'


def open_download(url: str):
  if urlparse(url).scheme != 'https':
    raise ValueError('Release downloads require HTTPS.')
  response = urlopen(Request(url, headers={'User-Agent': 'vamos-release/1', 'Accept-Encoding': 'identity'}), timeout=120)
  if urlparse(response.url).scheme != 'https':
    response.close()
    raise ValueError('Release download redirected outside HTTPS.')
  return response


def signed_json(url: str) -> tuple[bytes, dict]:
  try:
    from cryptography.exceptions import InvalidSignature

    from userspace.root.usr.lib.vamos.signatures import verify
  except ImportError as error:
    raise RuntimeError('Use the standalone CLI, or a Python environment with cryptography==50.0.2 via VAMOS_PYTHON.') from error

  with open_download(url) as response:
    raw = response.read(1024 * 1024 + 1)
  with open_download(url + '.sig') as response:
    signature = response.read(3310)
  if len(raw) > 1024 * 1024:
    raise ValueError('Release manifest is too large.')
  try:
    verify(raw, signature, PUBLIC_KEY.read_bytes())
  except InvalidSignature as error:
    raise ValueError('Release manifest signature is invalid.') from error
  value = json.loads(raw)
  if not isinstance(value, dict):
    raise TypeError('Invalid release manifest.')
  return raw, value


def validate_factory(manifest: dict, include_openpilot: bool) -> list[dict]:
  if manifest.get('product') != 'asius-v0':
    raise ValueError('Factory image is not for Asius v0.')
  check_version(manifest.get('version'))
  size = manifest['base']['image']['size']
  if type(size) is not int or size <= 0 or size % 4096:
    raise ValueError('Invalid factory image size.')
  begin, length = manifest['persist']['offset'], manifest['persist']['size']
  if type(begin) is not int or type(length) is not int or begin <= 0 or length <= 0 or begin + length >= size:
    raise ValueError('Invalid identity region.')
  erases = manifest['base']['operations'][:2]
  if erases != [{'operation': 'erase', 'offset': 0, 'size': begin},
                {'operation': 'erase', 'offset': begin + length, 'size': size - begin - length}]:
    raise ValueError('Factory erase ranges must preserve the identity region.')
  objects = [manifest['programmer']]
  for payload in ('base', 'openpilot') if include_openpilot else ('base',):
    for operation in operations_for_payload(manifest, payload):
      if operation['offset'] + operation['size'] > size:
        raise ValueError('Factory operation exceeds the image size.')
      if operation['operation'] == 'program':
        if operation.get('compression') != 'xz' or not HASH.fullmatch(operation.get('sha256', '')):
          raise ValueError('Invalid factory chunk format.')
        objects.append({'url': operation['url'], 'size': operation['compressed_size'], 'sha256': operation['compressed_sha256']})
  names = {}
  for obj in objects:
    if type(obj['size']) is not int or obj['size'] <= 0 or not HASH.fullmatch(obj['sha256']):
      raise ValueError('Invalid download size or hash.')
    parsed = urlparse(obj['url'])
    name = Path(parsed.path).name
    if parsed.scheme != 'https' or name in ('', '.', '..', 'flash.json', 'flash.json.sig'):
      raise ValueError('Invalid factory object URL.')
    if name in names and names[name] != (obj['size'], obj['sha256']):
      raise ValueError('Conflicting factory object names.')
    names[name] = (obj['size'], obj['sha256'])
  return objects


class Downloads:
  def __init__(self):
    self.bytes = 0
    self.seconds = 0.

  def verified(self, obj: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / Path(urlparse(obj['url']).path).name
    if path.is_file() and path.stat().st_size == obj['size']:
      with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() == obj['sha256']:
          return path
    started = time.monotonic()
    temporary = None
    try:
      with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
        temporary = Path(output.name)
        digest = hashlib.sha256()
        count = 0
        with open_download(obj['url']) as response:
          while block := response.read(1024 * 1024):
            count += len(block)
            if count > obj['size']:
              raise ValueError('Download exceeds its signed size.')
            output.write(block)
            digest.update(block)
        if count != obj['size'] or digest.hexdigest() != obj['sha256']:
          raise ValueError('Download failed size or SHA-256 verification.')
      os.replace(temporary, path)
      self.bytes += count
      self.seconds += time.monotonic() - started
      return path
    finally:
      if temporary is not None:
        temporary.unlink(missing_ok=True)

  def factory(self, cache: Path, version: str | None, include_openpilot: bool) -> Path:
    if version is None:
      _, latest = signed_json(LATEST_URL)
      version = check_version(latest.get('version'))
    raw, manifest = signed_json(factory_url(version))
    if manifest.get('version') != version:
      raise ValueError('Factory manifest does not match the selected release.')
    objects = validate_factory(manifest, include_openpilot)
    directory = cache / version
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    print(f'Downloading VamOS {version}' + (' with openpilot' if include_openpilot else ''), flush=True)
    for index, obj in enumerate(objects, 1):
      self.verified(obj, directory)
      print(f'\rDownload {index}/{len(objects)}', end='', flush=True)
    print()
    manifest_path = directory / 'flash.json'
    manifest_path.write_bytes(raw)
    return manifest_path
