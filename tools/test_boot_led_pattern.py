import re
import unittest
from pathlib import Path

from build.generate_boot_led_pattern import levels


class BootLedPatternTests(unittest.TestCase):
  def test_compiled_table_matches_generator(self):
    patch = (Path(__file__).resolve().parents[1] / "kernel/patches/0062-platform-asius-identity-leds.patch").read_text()
    table = patch.split("asius_boot_frames[180] = {", 1)[1].split("+};", 1)[0]
    values = [int(value) for value in re.findall(r"\d+", table)]
    self.assertEqual(values, [levels(frame / 60.)[0] for frame in range(180)])
    self.assertIn("asius_boot_color[3] = { 0, 0, 255 }", patch)

  def test_breath_has_visible_floor_on_all_eight_leds(self):
    self.assertEqual(levels(0), [1] * 8)
    self.assertEqual(levels(1.2), [6] * 8)
    for frame in range(180):
      values = levels(frame / 60.)
      self.assertEqual(values, levels(frame / 60. + 3.))
      self.assertEqual(len(values), 8)
      self.assertEqual(len(set(values)), 1)
      self.assertTrue(1 <= values[0] <= 6)


if __name__ == "__main__":
  unittest.main()
