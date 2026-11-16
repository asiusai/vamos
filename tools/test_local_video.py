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


class AccessPointTest(unittest.TestCase):
  def test_ap_lifecycle_preserves_station_and_does_not_require_a_saved_profile(self):
    present = {f'/sys/class/net/{video.INTERFACE}'}
    def command(*args, **kwargs):
      if args[:5] == ('iw', 'dev', video.INTERFACE, 'interface', 'add'):
        present.add(f'/sys/class/net/{video.AP_INTERFACE}')
      return ''
    with TemporaryDirectory() as directory, patch.object(video, 'ROOT', Path(directory)), \
         patch.object(Path, 'exists', lambda path: str(path) in present), \
         patch.object(video, 'command', side_effect=command) as run, \
         patch.object(video.AccessPoint, 'healthy', return_value=True), patch.object(video.subprocess, 'Popen') as spawn:
      spawn.return_value.poll.return_value = None
      ap = video.AccessPoint()
      ap.start('Asius-Live-test', 'test-password')
      ap.stop()
      run.assert_any_call('iw', 'dev', video.AP_INTERFACE, 'del', check=False)
      for call in run.call_args_list:
        if video.INTERFACE in call.args:
          self.assertEqual(call.args, ('iw', 'dev', video.INTERFACE, 'interface', 'add', video.AP_INTERFACE, 'type', '__ap'))

  def test_failed_start_removes_ap_and_private_config(self):
    present = {f'/sys/class/net/{video.INTERFACE}'}
    def command(*args, **kwargs):
      if args[:5] == ('iw', 'dev', video.INTERFACE, 'interface', 'add'):
        present.add(f'/sys/class/net/{video.AP_INTERFACE}')
      return ''
    with TemporaryDirectory() as directory, patch.object(video, 'ROOT', Path(directory)), \
         patch.object(Path, 'exists', lambda path: str(path) in present), \
         patch.object(video, 'command', side_effect=command) as run, \
         patch.object(video.subprocess, 'Popen') as spawn:
      spawn.return_value.poll.return_value = 1
      ap = video.AccessPoint()
      with self.assertRaises(RuntimeError):
        ap.start('Asius-Live-test', 'test-password')
      run.assert_any_call('iw', 'dev', video.AP_INTERFACE, 'del', check=False)
      self.assertEqual(list(Path(directory).glob('*.conf')), [])
      self.assertEqual(ap.children, [])

  def test_live_processes_do_not_hide_disabled_ap(self):
    ap = video.AccessPoint()
    ap.children = [Mock(), Mock()]
    for child in ap.children:
      child.poll.return_value = None
    with TemporaryDirectory() as directory, patch.object(video, 'ROOT', Path(directory)), \
         patch.object(video.socket, 'socket') as factory:
      client = factory.return_value.__enter__.return_value
      client.recv.return_value = b'state=DISABLED\n'
      self.assertFalse(ap.healthy())
      client.recv.return_value = b'state=ENABLED\nnum_sta[0]=0\n'
      self.assertTrue(ap.healthy())


if __name__ == '__main__':
  unittest.main()
