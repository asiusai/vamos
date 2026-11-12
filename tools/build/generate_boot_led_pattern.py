#!/usr/bin/env python3
"""Print the 60 Hz, three-second blue boot breath table used by patch 0062.

All eight road-board LEDs share one waveform.
They peak at 5/255 (approximately 2%) until camera exposure is available.
Keep this curve aligned with openpilot.selfdrive.v0.led_patterns.startup_levels.
"""
import math
import argparse
import re
from pathlib import Path


def levels(elapsed: float) -> list[int]:
  phase = elapsed % 3.
  if phase < 1.2:
    level = (1. - math.cos(math.pi * phase / 1.2)) / 2.
  else:
    level = (1. + math.cos(math.pi * (phase - 1.2) / 1.8)) / 2.
  brightness = math.floor((1. + 4. * level) * 257 + .5 + 1e-9)
  return [brightness] * 8


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--update', action='store_true', help='Regenerate the table in kernel patch 0062')
  if parser.parse_args().update:
    patch = Path(__file__).resolve().parents[2] / 'kernel/patches/0062-platform-asius-identity-leds.patch'
    rows = ['+\t' + ', '.join(str(levels(frame / 60.)[0]) for frame in range(start, start + 15)) + ','
            for start in range(0, 180, 15)]
    text, count = re.subn(r'(?<=asius_boot_frames\[180\] = \{\n).*?(?=\+};)', '\n'.join(rows) + '\n',
                          patch.read_text(), flags=re.DOTALL)
    assert count == 1
    patch.write_text(text)
  else:
    for frame in range(180):
      print(f'\t{levels(frame / 60.)[0]},')
