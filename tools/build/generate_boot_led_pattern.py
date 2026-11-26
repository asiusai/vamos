#!/usr/bin/env python3
"""Print the 60 Hz, 4.4-second blue boot comet table used by patch 0062.

Columns follow the eight road-board LEDs from left to right.
The peak is 5/255 (approximately 2%) until camera exposure is available.
Keep this curve aligned with openpilot.selfdrive.v0.led_patterns.startup_levels.
"""
import math
import argparse
import re
from pathlib import Path

PERIOD = 4.4
FRAME_COUNT = 264


def levels(elapsed: float) -> list[int]:
  phase = elapsed % PERIOD
  positions = [3.5 - 3.5 * math.cos(2. * math.pi * (phase - delay) / PERIOD)
               for delay in (0., .14, .28)]
  values = [sum(weight * math.exp(-.5 * ((index - position) / .82) ** 2)
                for weight, position in zip((1., .35, .12), positions, strict=True)) for index in range(8)]
  scale = max(1., max(values))
  return [round(value / scale * 1285) for value in values]


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--update', action='store_true', help='Regenerate the table in kernel patch 0062')
  if parser.parse_args().update:
    patch = Path(__file__).resolve().parents[2] / 'kernel/patches/0062-platform-asius-identity-leds.patch'
    rows = ['+\t{ ' + ', '.join(map(str, levels(frame / 60.))) + ' },' for frame in range(FRAME_COUNT)]
    text, count = re.subn(r'(?<=asius_boot_frames\[264\]\[8\] = \{\n).*?(?=\+};)', '\n'.join(rows) + '\n',
                          patch.read_text(), flags=re.DOTALL)
    assert count == 1
    prefix, source = text.split('+++ b/drivers/leds/trigger/ledtrig-asius-boot.c\n', 1)
    _, source = source.split('\n', 1)
    body, remaining = source.split('diff --git', 1)
    text = (prefix + '+++ b/drivers/leds/trigger/ledtrig-asius-boot.c\n' +
            f'@@ -0,0 +1,{len(body.splitlines())} @@\n' + body + 'diff --git' + remaining)
    patch.write_text(text)
  else:
    for frame in range(FRAME_COUNT):
      print('\t{ ' + ', '.join(map(str, levels(frame / 60.))) + ' },')
