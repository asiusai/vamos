import hashlib
import io
import json
import lzma
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools.flash import cli, devices, releases
from tools.flash.flash_factory import prepare_operations


def fixture(root):
  raw = b'x' * 4096
  compressed = lzma.compress(raw)
  (root / 'chunk.xz').write_bytes(compressed)
  (root / 'loader.bin').write_bytes(b'loader')
  chunk = {'operation': 'program', 'offset': 0, 'size': len(raw), 'compression': 'xz',
           'url': 'https://example.test/chunk.xz', 'compressed_size': len(compressed),
           'compressed_sha256': hashlib.sha256(compressed).hexdigest(), 'sha256': hashlib.sha256(raw).hexdigest()}
  return {'product': 'asius-v0', 'version': '18.11', 'manifest_version': 3, 'sector_size': 4096,
          'persist': {'offset': 4096, 'size': 4096},
          'programmer': {'url': 'https://example.test/loader.bin', 'size': 6, 'sha256': hashlib.sha256(b'loader').hexdigest()},
          'base': {'image': {'size': 16384}, 'operations': [
            {'operation': 'erase', 'offset': 0, 'size': 4096},
            {'operation': 'erase', 'offset': 8192, 'size': 8192}, chunk]},
          'optional_payloads': {'openpilot': {'operations': [{**chunk, 'offset': 8192, 'url': 'https://example.test/missing.xz'}]}}}


class DevicesTest(unittest.TestCase):
  def test_only_dragon_devices_and_negotiated_link_speed(self):
    with tempfile.TemporaryDirectory() as temporary:
      root = Path(temporary)
      entries = [('1-1', '05c6', '9008', 'Qualcomm EDL', '480'),
                 ('2-1', '3801', '9008', 'Asius', '5000'),
                 ('3-1', '1d6b', '0103', 'Dragon Q6A NCM', '10000'),
                 ('4-1', '3801', '0001', 'Chestnut', '5000'),
                 ('5-1', '1d6b', '0103', 'Other gadget', '5000')]
      for port, vid, pid, product, speed in entries:
        path = root / port
        path.mkdir()
        for name, value in {'idVendor': vid, 'idProduct': pid, 'product': product, 'speed': speed}.items():
          (path / name).write_text(value)
      result = devices.devices(root)
      self.assertEqual([(d.port, d.mode, d.speed) for d in result],
                       [('1-1', 'EDL', 'USB 2'), ('2-1', 'EDL', 'USB 3'), ('3-1', 'NCM', 'USB 3')])

  def test_refuses_missing_or_ambiguous_edl(self):
    device = devices.Device('1-1', 'serial', 'EDL', 'USB 3', 'Dragon', 0x05c6, 0x9008)
    for available in ([], [device, device]):
      with self.subTest(count=len(available)), patch.object(devices, 'devices', return_value=available), self.assertRaises(ValueError):
        devices.select_edl()


class CliTest(unittest.TestCase):
  def test_commands_and_default_payload(self):
    for argv, command in [(['flash'], 'flash'), (['flash', '--remote', '18.11'], 'flash'),
                          (['flash', '--remote'], 'flash'), (['uboot'], 'uboot'), (['devices'], 'devices')]:
      args = cli.parser().parse_args(argv)
      self.assertEqual(args.command, command)
      if command.startswith('flash'):
        self.assertFalse(args.no_openpilot)
        self.assertTrue(cli.parser().parse_args([*argv, '--no-openpilot']).no_openpilot)

  def test_source_selection(self):
    self.assertIsNone(cli.parser().parse_args(['flash']).remote)
    self.assertEqual(cli.parser().parse_args(['flash', '--remote']).remote, '')
    self.assertEqual(cli.parser().parse_args(['flash', '--remote', '18.11']).remote, '18.11')
    args = cli.parser().parse_args(['flash', '--manifest', '/tmp/custom.json', '--no-openpilot'])
    self.assertEqual(args.manifest, Path('/tmp/custom.json'))
    self.assertTrue(args.no_openpilot)

  def test_rejects_old_targets_and_invalid_versions(self):
    for argv in (['flash', 'all'], ['flash', 'system'], ['flash', 'uboot'], ['uboot', '--without-openpilot'],
                 ['flash', '--remote', '../escape'], ['flash', '--remote', 'https://example.test'],
                 ['flash-local'], ['flash-version', '18.11'], ['flash', '--without-openpilot'],
                 ['flash', '--remote', '--manifest', 'local.json']):
      with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
        cli.parser().parse_args(argv)

  def test_no_device_never_downloads_or_opens_firehose(self):
    with tempfile.TemporaryDirectory() as temporary, patch.dict('os.environ', {'XDG_CACHE_HOME': temporary}), \
         patch.object(cli, 'select_edl', side_effect=ValueError('No EDL')), patch.object(cli, 'install_factory') as install, \
         redirect_stderr(io.StringIO()):
      self.assertEqual(cli.main(['flash', '--yes']), 1)
      install.assert_not_called()

  def test_device_replacement_stops_before_usb_access(self):
    before = devices.Device('1-1', '-', 'EDL', 'USB 3', 'Dragon', 0x05c6, 0x9008, 1, 2)
    after = devices.Device('1-1', '-', 'EDL', 'USB 3', 'Dragon', 0x05c6, 0x9008, 1, 3)
    with patch.object(cli, 'select_edl', return_value=after), self.assertRaisesRegex(ValueError, 'device changed'):
      cli.access_device(before)


class ReleaseTest(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.manifest = fixture(self.root)

  def test_local_base_needs_no_openpilot_file(self):
    releases.validate_factory(self.manifest, False)
    path = self.root / 'flash.json'
    path.write_text(json.dumps(self.manifest))
    with redirect_stdout(io.StringIO()):
      sector, operations = prepare_operations(path, self.root / 'prepared', False)
    self.assertEqual(sector, 4096)
    self.assertEqual(len(operations), 3)
    self.assertEqual(operations[-1][1].read_bytes(), b'x' * 4096)

  def test_rejects_bad_ranges_before_downloads(self):
    mutations = [lambda m: m['base']['operations'][0].update(size=8192),
                 lambda m: m['base']['operations'][2].update(offset=4096),
                 lambda m: m['base']['operations'][2].update(offset=16384),
                 lambda m: m.update(product='other'),
                 lambda m: m['programmer'].update(url='http://example.test/loader.bin')]
    for mutate in mutations:
      manifest = json.loads(json.dumps(self.manifest))
      mutate(manifest)
      with self.subTest(manifest=manifest), self.assertRaises(ValueError):
        releases.validate_factory(manifest, False)

  def test_cache_is_verified_and_corrupt_download_is_not_saved(self):
    downloads = releases.Downloads()
    obj = self.manifest['programmer']
    with patch.object(releases, 'open_download') as network:
      self.assertEqual(downloads.verified(obj, self.root).read_bytes(), b'loader')
      network.assert_not_called()
    (self.root / 'loader.bin').write_bytes(b'broken')
    with patch.object(releases, 'open_download', return_value=io.BytesIO(b'broken')), self.assertRaises(ValueError):
      downloads.verified(obj, self.root)
    self.assertEqual((self.root / 'loader.bin').read_bytes(), b'broken')
    with patch.object(releases, 'open_download', return_value=io.BytesIO(b'loader')):
      self.assertEqual(downloads.verified(obj, self.root).read_bytes(), b'loader')
    self.assertEqual(downloads.bytes, 6)

  def test_latest_and_custom_release_select_same_factory_manifest(self):
    manifest = self.manifest
    for version in (None, '18.11'):
      def signed(url):
        value = {'version': '18.11'} if url == releases.LATEST_URL else manifest
        return json.dumps(value).encode(), value
      downloads = releases.Downloads()
      with patch.object(releases, 'signed_json', side_effect=signed) as request, \
           patch.object(downloads, 'verified'), redirect_stdout(io.StringIO()):
        path = downloads.factory(self.root / 'cache', version, False)
      self.assertEqual(json.loads(path.read_text()), manifest)
      self.assertEqual(request.call_args.args, (releases.factory_url('18.11'),))
      self.assertEqual(request.call_count, 2 if version is None else 1)

  def test_signed_manifest_rejects_changed_bytes(self):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.mldsa import MLDSA65PrivateKey
    key = MLDSA65PrivateKey.generate()
    public = self.root / 'public.pem'
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    payload = b'{"version":"18.11"}'
    signature = key.sign(payload)
    with patch.object(releases, 'PUBLIC_KEY', public):
      with patch.object(releases, 'open_download', side_effect=[io.BytesIO(payload), io.BytesIO(signature)]):
        self.assertEqual(releases.signed_json('https://example.test/release')[1]['version'], '18.11')
      with patch.object(releases, 'open_download', side_effect=[io.BytesIO(b'{"version":"18.12"}'), io.BytesIO(signature)]), \
           self.assertRaisesRegex(ValueError, 'signature is invalid'):
        releases.signed_json('https://example.test/release')


if __name__ == '__main__':
  unittest.main()
