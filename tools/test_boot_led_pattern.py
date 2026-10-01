import re
import unittest
from pathlib import Path

from build.generate_boot_led_pattern import levels


class BootLedPatternTests(unittest.TestCase):
  def test_compiled_table_matches_generator(self):
    patch = (Path(__file__).resolve().parents[1] / "kernel/patches/0062-platform-asius-identity-leds.patch").read_text()
    table = patch.split("asius_boot_frames[180][6] = {", 1)[1].split("+};", 1)[0]
    rows = [[int(value) for value in row.split(",")] for row in re.findall(r"\{ ([\d, ]+) \}", table)]
    self.assertEqual(rows, [levels(frame / 60.) for frame in range(180)])
    self.assertIn("asius_boot_color[3] = { 0, 0, 255 }", patch)

  def test_breath_has_visible_floor_and_unused_centers(self):
    self.assertEqual(levels(0), [2, 0, 2, 2, 0, 2])
    self.assertEqual(levels(1.2), [25, 0, 25, 25, 0, 25])
    for frame in range(180):
      values = levels(frame / 60.)
      self.assertEqual(values, levels(frame / 60. + 3.))
      self.assertEqual([values[1], values[4]], [0, 0])
      self.assertEqual(len({values[i] for i in (0, 2, 3, 5)}), 1)
      self.assertTrue(2 <= values[0] <= 25)


if __name__ == "__main__":
  unittest.main()
