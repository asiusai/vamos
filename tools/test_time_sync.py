"""Exercise boot recovery, NTP freshness and corrections without setting host time."""
import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('vamos_clock', ROOT / 'userspace/root/usr/lib/vamos/clock.py')
clock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clock)
BUILD = 1788710400


class ClockTest(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    root = Path(self.temp.name)
    for name, filename in [('STATE', 'state.json'), ('RUNTIME', 'runtime.json'), ('SYNCED', 'synced'), ('BUILD', 'build'), ('BOOT_ID', 'boot')]:
      p = patch.object(clock, name, root / filename)
      p.start()
      self.addCleanup(p.stop)
    clock.BUILD.write_text(str(BUILD))
    clock.BOOT_ID.write_text('test-boot')

  def test_offline_boot_uses_only_the_saved_checkpoint(self):
    self.assertEqual(clock.boot_floor(BUILD, BUILD + 10 * 86400), BUILD + 10 * 86400)
    for saved in (None, 'corrupt', float('nan'), BUILD - 1):
      self.assertEqual(clock.boot_floor(BUILD, saved), BUILD)

  def test_boot_ignores_missing_stale_and_plausible_future_hardware_time(self):
    for hardware_time in (0, BUILD, BUILD + 86400, 5762161462):
      clock.atomic_json(clock.STATE, {'unixTime': BUILD + 100})
      with patch.object(clock.time, 'time', return_value=hardware_time), patch.object(clock.time, 'clock_settime') as step, patch.object(clock, 'checkpoint'):
        clock.restore()
      step.assert_called_once_with(clock.time.CLOCK_REALTIME, BUILD + 100)
      # Drop only this test's per-boot marker before simulating another boot.
      clock.RUNTIME.unlink()

  def test_restore_only_once_per_boot(self):
    clock.atomic_json(clock.STATE, {'unixTime': BUILD + 100})
    with patch.object(clock.time, 'time', return_value=BUILD), patch.object(clock.time, 'clock_settime') as step:
      clock.restore()
      clock.restore()
    step.assert_called_once_with(clock.time.CLOCK_REALTIME, BUILD + 100)
    self.assertEqual(clock.read_json(clock.STATE)['unixTime'], BUILD + 100)

  def test_backwards_samples_slew_instead_of_step(self):
    with patch.object(clock.time, 'time', return_value=BUILD + 300), patch.object(clock.time, 'clock_settime') as step, patch.object(clock, 'slew') as slew:
      self.assertEqual(clock.synchronize(BUILD + 100, 'ntp')['correction'], 'slew')
    step.assert_not_called()
    slew.assert_called_once_with(-200)
    self.assertTrue(clock.SYNCED.exists())

  def test_forward_sample_and_phone_cannot_override_recent_ntp(self):
    with patch.object(clock.time, 'time', return_value=BUILD), patch.object(clock.time, 'clock_settime') as step, patch.object(clock, 'slew'):
      self.assertTrue(clock.synchronize(BUILD + 100, 'ntp')['accepted'])
      self.assertFalse(clock.synchronize(BUILD + 500, 'app')['accepted'])
    step.assert_called_once_with(clock.time.CLOCK_REALTIME, BUILD + 100)

  def test_phone_works_offline_and_checkpoint_never_regresses(self):
    clock.atomic_json(clock.STATE, {'unixTime': BUILD + 100})
    with patch.object(clock.time, 'time', return_value=BUILD + 99), patch.object(clock.time, 'clock_settime'), patch.object(clock, 'slew'):
      self.assertTrue(clock.synchronize(BUILD + 200, 'app')['accepted'])
      clock.checkpoint()
    self.assertEqual(clock.read_json(clock.STATE)['unixTime'], BUILD + 100)

  def test_invalid_samples_never_touch_clock(self):
    with patch.object(clock.time, 'clock_settime') as step, patch.object(clock, 'slew') as slew:
      for value in (0, BUILD - 1, float('nan'), float('inf'), BUILD + clock.TEN_YEARS + 1):
        with self.assertRaises(ValueError):
          clock.synchronize(value, 'app')
    step.assert_not_called()
    slew.assert_not_called()

  def packet(self):
    packet = bytearray(48)
    packet[0] = 0x24
    packet[1] = 2
    packet[24:32] = b'12345678'
    packet[32:40] = packet[40:48] = struct.pack('!II', BUILD + clock.NTP_EPOCH, 0)
    return packet

  def test_ntp_matches_fresh_request_and_compensates_delay(self):
    self.assertAlmostEqual(clock.parse_ntp(self.packet(), b'12345678', .1, BUILD), BUILD + .05)
    with self.assertRaises(ValueError):
      clock.parse_ntp(self.packet(), b'oldtoken', .1, BUILD)
    for index, value in [(0, 0xe4), (0, 0x23), (1, 0), (1, 16)]:
      packet = self.packet()
      packet[index] = value
      with self.assertRaises(ValueError):
        clock.parse_ntp(packet, b'12345678', .1, BUILD)
    with self.assertRaises(ValueError):
      clock.parse_ntp(self.packet(), b'12345678', 3, BUILD)

  def test_ntp_era_rollover(self):
    after_rollover = 2208988800  # 2040
    raw = struct.pack('!II', (after_rollover + clock.NTP_EPOCH) % clock.ERA, 0)
    self.assertEqual(clock.ntp_seconds(raw, after_rollover), after_rollover)


if __name__ == '__main__':
  unittest.main()
