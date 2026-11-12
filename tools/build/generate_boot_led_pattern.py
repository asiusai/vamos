#!/usr/bin/env python3
"""Print the 60 Hz, three-second blue boot breath table used by patch 0062.

All eight road-board LEDs share one waveform.
They peak at 6/255 (approximately 2.4%) until camera exposure is available.
Keep this curve aligned with openpilot.selfdrive.v0.led_patterns.startup_levels.
"""
import math


def levels(elapsed: float) -> list[int]:
  phase = elapsed % 3.
  if phase < 1.2:
    level = (1. - math.cos(math.pi * phase / 1.2)) / 2.
  else:
    level = (1. + math.cos(math.pi * (phase - 1.2) / 1.8)) / 2.
  brightness = math.floor(1. + 5. * level + .5 + 1e-9)
  return [brightness] * 8


if __name__ == '__main__':
  for frame in range(180):
    print(f'\t{levels(frame / 60.)[0]},')
