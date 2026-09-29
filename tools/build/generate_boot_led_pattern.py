#!/usr/bin/env python3
"""Print the 30 Hz, three-second boot fade table used by patch 0062.

The six columns are LEDs 1-6 at 0, 7.5, 15, 45, 52.5, 60 mm.
The two center LEDs use 10% brightness.
The outer LEDs peak at 25/255 (under 10%) until camera exposure is available.
Keep this curve aligned with openpilot.selfdrive.v0.led_patterns.startup_levels.
"""
import math


def levels(elapsed: float) -> list[int]:
  phase = (elapsed % 3.) / 3.
  brightness = round(25. * (1. - math.cos(math.tau * phase)) / 2.)
  return [round(brightness * scale) for scale in (1., 0.1, 1., 1., 0.1, 1.)]


if __name__ == '__main__':
  for frame in range(90):
    print('\t{ ' + ', '.join(str(value) for value in levels(frame / 30.)) + ' },')
