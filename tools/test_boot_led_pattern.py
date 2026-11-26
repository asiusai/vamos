import re
import unittest
from pathlib import Path

from build.generate_boot_led_pattern import FRAME_COUNT, PERIOD, levels


class BootLedPatternTests(unittest.TestCase):
  def test_compiled_table_matches_generator(self):
    patch = (Path(__file__).resolve().parents[1] / "kernel/patches/0062-platform-asius-identity-leds.patch").read_text()
    table = patch.split("asius_boot_frames[264][8] = {", 1)[1].split("+};", 1)[0]
    values = [int(value) for value in re.findall(r"\d+", table)]
    self.assertEqual(values, [value for frame in range(FRAME_COUNT) for value in levels(frame / 60.)])
    self.assertIn("asius_boot_color[3] = { 0, 0, 255 }", patch)

  def test_comet_moves_across_all_leds_with_smooth_reversals(self):
    frames = [levels(frame / 60.) for frame in range(FRAME_COUNT + 1)]
    self.assertEqual(frames[0].index(max(frames[0])), 0)
    self.assertEqual(frames[132].index(max(frames[132])), 7)
    self.assertEqual(frames[0], frames[-1])
    self.assertEqual({row.index(max(row)) for row in frames}, set(range(8)))
    self.assertLess(max(abs(b - a) for before, after in zip(frames, frames[1:])
                        for a, b in zip(before, after)), 129)
    self.assertEqual(frames[0][4:], [0] * 4)
    for frame in range(FRAME_COUNT):
      values = levels(frame / 60.)
      self.assertEqual(values, levels(frame / 60. + PERIOD))
      self.assertEqual(len(values), 8)
      self.assertTrue(all(0 <= value <= 1285 for value in values))
    self.assertGreater(len({value for row in frames for value in row}), 100)

  def test_device_tree_triggers_follow_physical_left_to_right_order(self):
    patch = (Path(__file__).resolve().parents[1] / 'kernel/patches/0054-arm64-dts-asius-v0.patch').read_text()
    assignments = re.findall(r'label = "(asius:cam[23]:\w+:\d)";(?:(?!label =).)*?'
                             r'linux,default-trigger = "(asius-boot-\d-[rgb])";', patch, re.DOTALL)
    expected = {}
    for number, (camera, package) in enumerate([(3, 3), (3, 2), (3, 1), (3, 0),
                                               (2, 3), (2, 2), (2, 1), (2, 0)], 1):
      for color in ('red', 'green', 'blue'):
        expected[f'asius:cam{camera}:{color}:{package}'] = f'asius-boot-{number}-{color[0]}'
    self.assertEqual(len(assignments), 24)
    self.assertEqual(dict(assignments), expected)


if __name__ == "__main__":
  unittest.main()
