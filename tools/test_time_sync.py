"""Exercise boot recovery, authenticated time and corrections without setting host time."""
import importlib.util
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
      self.assertEqual(clock.synchronize(BUILD + 100, 'nts')['correction'], 'slew')
    step.assert_not_called()
    slew.assert_called_once_with(-200)
    self.assertTrue(clock.SYNCED.exists())

  def test_forward_sample_and_phone_cannot_override_recent_ntp(self):
    with patch.object(clock.time, 'time', return_value=BUILD), patch.object(clock.time, 'clock_settime') as step, patch.object(clock, 'slew'):
      self.assertTrue(clock.synchronize(BUILD + 100, 'nts')['accepted'])
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

  def test_authenticated_tracking_freshness(self):
    sample = f'01020304,1.2.3.4,3,{BUILD},100,0,0,0,0,0,0.02,0.01,16,Normal'
    self.assertEqual(clock.parse_chrony_tracking(sample, BUILD - 100), BUILD)
    for invalid in (sample.replace('Normal', 'Not synchronised'), sample.replace('01020304', '00000000'),
                    sample.replace(',100,', ',nan,'), sample.replace(',0.01,', ',10,')):
      with self.assertRaises(ValueError):
        clock.parse_chrony_tracking(invalid, BUILD - 100)
    with self.assertRaises(ValueError):
      clock.parse_chrony_tracking(sample, BUILD + 1000)

  def test_real_chrony_slow_clock_sample_advances_time(self):
    # Captured after Asius-1 reboot. Chronyc reports 14.893 seconds slow.
    sample = 'A29FC87B,162.159.200.123,4,1791994582.681614496,14.893366814,0.000320654,0.003796908,35.991,0.259,11.466,0.013530743,0.001582239,16.3,Normal'
    now = 1791994581.3006172
    self.assertAlmostEqual(clock.parse_chrony_tracking(sample, now), now + 14.893366814)

  def test_fast_clock_sample_reduces_target_without_backwards_step(self):
    sample = f'01020304,1.2.3.4,3,{BUILD},-100,0,0,0,0,0,0.02,0.01,16,Normal'
    now = BUILD + 100
    target = clock.parse_chrony_tracking(sample, now)
    self.assertEqual(target, BUILD)
    with patch.object(clock.time, 'time', return_value=now), patch.object(clock.time, 'clock_settime') as step, patch.object(clock, 'slew') as slew:
      clock.synchronize(target, 'nts')
    step.assert_not_called()
    slew.assert_called_once_with(-100)


if __name__ == '__main__':
  unittest.main()
