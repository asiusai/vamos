#!/usr/bin/env python3
"""Print the 30 Hz, three-second boot sweep table used by patch 0062.

The six columns are LEDs 1-6 at 0, 7.5, 15, 45, 52.5, 60 mm.
The two center LEDs use 10% brightness.
Keep this curve aligned with openpilot.selfdrive.v0.led_patterns.startup_levels.
"""
import math


def smooth(value: float) -> float:
  value = max(0., min(1., value))
  return value * value * (3. - 2. * value)


def levels(elapsed: float) -> list[int]:
  def position(t: float) -> float:
    return 60. - abs((t * 40.) % 120. - 60.)

  def glow(x: float, t: float) -> float:
    head = position(t)
    width = 5. + 7. * smooth(min(head, 60. - head) / 15.)
    return math.exp(-0.5 * ((x - head) / width) ** 2)

  head = position(elapsed)
  tail_fade = smooth(min(head, 60. - head) / 15.)
  result = []
  for index, x in enumerate((0., 7.5, 15., 45., 52.5, 60.)):
    tail = max(math.exp(-age / 0.45) * glow(x, elapsed - age) for age in (0.1, 0.2, 0.3, 0.4, 0.6, 0.8))
    result.append(round(round(255. * max(glow(x, elapsed), tail_fade * tail)) * (0.1 if index in (1, 4) else 1.)))
  return result


if __name__ == '__main__':
  for frame in range(90):
    print('\t{ ' + ', '.join(str(value) for value in levels(frame / 30.)) + ' },')
