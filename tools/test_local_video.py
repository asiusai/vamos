"""Lease ownership and crash cleanup for device-local video Wi-Fi."""
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'userspace/root/usr/bin/vamos-local-video'
spec = importlib.util.spec_from_loader('local_video', importlib.machinery.SourceFileLoader('local_video', str(SOURCE)))
video = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video)


class LocalVideoTest(unittest.TestCase):
  def setUp(self):
    mac = patch.object(Path, 'read_bytes', return_value=b'00:11:22:33:44:55\n')
    mac.start()
    self.addCleanup(mac.stop)
    self.ap = Mock()
    self.ap.healthy.return_value = True
    self.session = video.Session(self.ap)

  def test_only_owner_can_renew_or_stop(self):
    first = self.session.request({'action': 'start', 'owner': 'phone'})
    self.assertEqual(self.session.request({'action': 'start', 'owner': 'phone'}), first)
    self.ap.start.assert_called_once()
    for action in ('start', 'renew', 'stop', 'check'):
      with self.assertRaises(ValueError):
        self.session.request({'action': action, 'owner': 'other', 'lease': first['lease']})
    with self.assertRaises(ValueError):
      self.session.request({'action': 'stop', 'owner': 'phone', 'lease': 'wrong'})
    self.ap.stop.assert_not_called()
    self.session.request({'action': 'stop', 'owner': 'phone', 'lease': first['lease']})
    self.ap.stop.assert_called_once()

  def test_lease_expires_without_phone_and_new_session_changes_credentials(self):
    with patch.object(video.time, 'monotonic', return_value=0):
      first = self.session.request({'action': 'start', 'owner': 'phone'})
    with patch.object(video.time, 'monotonic', return_value=video.LEASE_SECONDS + 1):
      self.session.expire()
      self.ap.stop.assert_called_once()
      second = self.session.request({'action': 'start', 'owner': 'phone'})
      self.assertNotEqual(first['password'], second['password'])
      self.assertNotEqual(first['lease'], second['lease'])
      with self.assertRaises(ValueError):
        self.session.request({'action': 'renew', 'owner': 'phone', 'lease': first['lease']})

  def test_failed_radio_process_releases_session(self):
    first = self.session.request({'action': 'start', 'owner': 'phone'})
    self.ap.healthy.return_value = False
    with self.assertRaises(ValueError):
      self.session.request({'action': 'renew', 'owner': 'phone', 'lease': first['lease']})
    self.ap.stop.assert_called_once()


class OfflineAccessPointTest(unittest.TestCase):
  def test_start_without_active_network_profile(self):
    def command(*args, **kwargs):
      if args[:3] == ('nmcli', '-g', 'GENERAL.NM-MANAGED'):
        return 'yes'
      # A disconnected device has no GENERAL.CON-UUID value.
      return ''

    with TemporaryDirectory() as directory, patch.object(video, 'ROOT', Path(directory)), \
         patch.object(Path, 'exists', return_value=True), patch.object(video, 'command', side_effect=command), \
         patch.object(video.time, 'sleep'), patch.object(video.subprocess, 'Popen') as spawn:
      spawn.return_value.poll.return_value = None
      ap = video.AccessPoint()
      ap.start('Asius-Live-test', 'test-password')
      self.assertEqual(ap.restore, {'uuid': ''})
      self.assertTrue(ap.healthy())
      ap.stop()
      self.assertIsNone(ap.restore)


if __name__ == '__main__':
  unittest.main()
