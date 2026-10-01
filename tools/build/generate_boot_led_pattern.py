#!/usr/bin/env python3
"""Print the 60 Hz, three-second blue boot breath table used by patch 0062.

The six columns are LEDs 1-6 at 0, 7.5, 15, 45, 52.5, 60 mm.
The two center LEDs stay off.
The outer LEDs peak at 25/255 (under 10%) until camera exposure is available.
Keep this curve aligned with openpilot.selfdrive.v0.led_patterns.startup_levels.
"""
import math


def levels(elapsed: float) -> list[int]:
  phase = elapsed % 3.
  if phase < 1.2:
    level = (1. - math.cos(math.pi * phase / 1.2)) / 2.
  else:
    level = (1. + math.cos(math.pi * (phase - 1.2) / 1.8)) / 2.
  brightness = math.floor(2. + 23. * level + .5 + 1e-9)
  return [brightness, 0, brightness, brightness, 0, brightness]


if __name__ == '__main__':
  for frame in range(180):
    print('\t{ ' + ', '.join(str(value) for value in levels(frame / 60.)) + ' },')
