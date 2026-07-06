#!/usr/bin/env python3
"""Bounded OS04C10 register sweep for cam-v1 bring-up.

This script intentionally searches for the earliest useful signal: an override
set that keeps OS04C10 I2C/readback sane after the final stream-enable write.
Only candidates that survive that boundary are worth trying through CAMSS/RDI.

Copy this file next to os04c10_camthink_bringup.py on the Dragon, then run:

  sudo python3 /tmp/os04c10_reg_sweep.py --camera cam2 --profile all

Optional capture on passing candidates:

  sudo python3 /tmp/os04c10_reg_sweep.py --camera cam2 --profile all --run-rdi
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from os04c10_camthink_bringup import (  # noqa: E402
  CAMERAS,
  CAMTHINK_REGS,
  KEY_REGS,
  compare_values,
  expected_subset,
  final_expected,
  find_subdev,
  force_subdev_power,
  fmt_value,
  read_many,
  rebind,
  write_one,
  write_sequence,
)


ID_REGS = [0x300a, 0x300b, 0x300c]


@dataclass(frozen=True)
class Candidate:
  name: str
  overrides: tuple[tuple[int, int], ...]


def regs_to_text(regs: list[tuple[int | None, int]]) -> str:
  lines = []
  for reg, value in regs:
    if reg is None:
      lines.append(f"delay={value}")
    else:
      lines.append(f"0x{reg:04x}=0x{value & 0xff:02x}")
  return "\n".join(lines) + "\n"


def final_value_map(regs: list[tuple[int | None, int]]) -> dict[int, int]:
  values: dict[int, int] = {}
  for reg, value in regs:
    if reg is not None:
      values[reg] = value & 0xff
  return values


def add_case(cases: list[Candidate], seen: set[tuple[tuple[int, int], ...]], name: str, values: list[tuple[int, int]]) -> None:
  merged: dict[int, int] = {}
  order: list[int] = []
  for reg, value in values:
    if reg not in merged:
      order.append(reg)
    merged[reg] = value & 0xff
  normalized = tuple((reg, merged[reg]) for reg in order)
  key = tuple(sorted(normalized))
  if key in seen:
    return
  seen.add(key)
  cases.append(Candidate(name, normalized))


def build_candidates(profile: str) -> list[Candidate]:
  cases: list[Candidate] = []
  seen: set[tuple[tuple[int, int], ...]] = set()

  camthink = final_value_map(list(CAMTHINK_REGS))

  # Values pulled from CamThink's os04c10_reg.h names and os04c10.c helper
  # functions at commit 547ac0f. These are not all safe as a final config, but
  # they are useful discriminators for "stream-enable kills readback".
  mipi_helper = [
    (0x3017, 0x00), (0x3018, 0x00), (0x302e, 0x08), (0x4837, 0x23),
    (0x3034, 0x18), (0x3035, 0x12), (0x3036, 0x30), (0x3037, 0x13),
    (0x3108, 0x01), (0x4814, 0x2a), (0x4800, 0x24), (0x3019, 0x70),
    (0x300e, 0x45), (0x4202, 0x00),
  ]
  dvp_helper = [
    (0x3017, 0xff), (0x3018, 0xf3), (0x302e, 0x00), (0x471c, 0x50),
    (0x300e, 0x58), (0x3034, 0x18), (0x3035, 0x41), (0x3036, 0x60),
    (0x3037, 0x13), (0x3108, 0x01),
  ]

  # Values from comma/openpilot's OS04C10 4-lane 12-bit table. The full table is
  # known not to be our desired two-lane mode, but its PLL/MIPI timing values are
  # worth testing against the CamThink two-lane base.
  comma_pll = [
    (0x0301, 0xe4), (0x0303, 0x01), (0x0305, 0xb6), (0x0306, 0x01),
    (0x0307, 0x17), (0x0323, 0x04), (0x0324, 0x01), (0x0325, 0x62),
  ]
  comma_mipi = [
    (0x3016, 0x72), (0x3106, 0x21), (0x4803, 0x00), (0x480e, 0x04),
    (0x4813, 0xe4), (0x4823, 0x3f), (0x4825, 0x30), (0x4837, 0x15),
    (0x484b, 0x27),
  ]
  camthink_mipi_io = [
    (0x3017, 0x00), (0x3018, 0x00), (0x302e, 0x08), (0x4837, 0x23),
    (0x4814, 0x2a), (0x4800, 0x24), (0x3019, 0x70), (0x300e, 0x45),
    (0x4202, 0x00),
  ]

  add_case(cases, seen, "baseline_camthink", [])
  add_case(cases, seen, "camthink_mipi_helper", mipi_helper)
  add_case(cases, seen, "camthink_dvp_helper", dvp_helper)
  add_case(cases, seen, "comma_pll", comma_pll)
  add_case(cases, seen, "comma_mipi", comma_mipi)
  add_case(cases, seen, "comma_pll_mipi", comma_pll + comma_mipi)
  add_case(cases, seen, "mipi_helper_plus_comma_pll", comma_pll + mipi_helper)

  single_values = {
    # Reset/clock/PLL and system-divider controls named in the CamThink header.
    0x0301: [camthink.get(0x0301, 0x84), 0xe4, 0x44, 0x64, 0xa4, 0xc4],
    0x0305: [camthink.get(0x0305, 0x5b), 0xb6, 0x2d, 0x40, 0x80],
    0x0306: [camthink.get(0x0306, 0x00), 0x01],
    0x0307: [camthink.get(0x0307, 0x17), 0x10, 0x13, 0x1b],
    0x3034: [0x18, 0x00, 0x30],
    0x3035: [0x12, 0x41, 0x5b, 0xb6],
    0x3036: [0x30, 0x60, 0x00],
    0x3037: [0x13, 0x17, 0x00],
    0x3038: [0x00, 0x01],
    0x3039: [0x00, 0x01],
    0x303a: [0x00, 0x01],
    0x303b: [0x00, 0x01],
    0x303c: [0x00, 0x01],
    0x303d: [0x00, 0x01],
    0x3108: [0x01, 0x11, 0x21, 0x31],
    # Pad/lane/MIPI mode controls.
    0x300e: [0x00, 0x45, 0x58],
    0x3012: [camthink.get(0x3012, 0x06), 0x04, 0x02, 0x00],
    0x3013: [camthink.get(0x3013, 0x02), 0x00, 0x01, 0x03],
    0x3016: [camthink.get(0x3016, 0x32), 0x72, 0x00],
    0x3017: [0x00, 0xff],
    0x3018: [0x00, 0xf3],
    0x3019: [0x00, 0x70],
    0x3021: [camthink.get(0x3021, 0x03), 0x23, 0x00],
    0x302e: [0x08, 0x00],
    0x4202: [0x00, 0x0f],
    0x4305: [camthink.get(0x4305, 0x83), 0x03, 0x80],
    0x4800: [0x00, 0x24],
    0x4803: [camthink.get(0x4803, 0x10), 0x00],
    0x4809: [camthink.get(0x4809, 0x1e), 0x0e],
    0x480a: [camthink.get(0x480a, 0x04), 0x00],
    0x480c: [camthink.get(0x480c, 0x32), 0x00],
    0x480e: [camthink.get(0x480e, 0x00), 0x04],
    0x4813: [camthink.get(0x4813, 0x00), 0xe4],
    0x4814: [0x00, 0x2a],
    0x4819: [camthink.get(0x4819, 0x70), 0x00],
    0x481f: [camthink.get(0x481f, 0x30), 0x00],
    0x4823: [camthink.get(0x4823, 0x3c), 0x3f, 0x00],
    0x4825: [camthink.get(0x4825, 0x32), 0x30, 0x00],
    0x4833: [camthink.get(0x4833, 0x10), 0x00],
    0x4837: [camthink.get(0x4837, 0x0a), 0x0e, 0x15, 0x23],
    0x484b: [camthink.get(0x484b, 0x07), 0x27],
  }

  if profile in ("all", "single", "pll"):
    for reg in [0x0301, 0x0305, 0x0306, 0x0307, 0x3034, 0x3035, 0x3036,
                0x3037, 0x3038, 0x3039, 0x303a, 0x303b, 0x303c, 0x303d, 0x3108]:
      for value in single_values[reg]:
        if value != camthink.get(reg):
          add_case(cases, seen, f"single_{reg:04x}_{value:02x}", [(reg, value)])

  if profile in ("all", "single", "mipi"):
    for reg in [0x300e, 0x3012, 0x3013, 0x3016, 0x3017, 0x3018, 0x3019,
                0x3021, 0x302e, 0x4202, 0x4305, 0x4800, 0x4803, 0x4809,
                0x480a, 0x480c, 0x480e, 0x4813, 0x4814, 0x4819, 0x481f,
                0x4823, 0x4825, 0x4833, 0x4837, 0x484b]:
      for value in single_values[reg]:
        if value != camthink.get(reg):
          add_case(cases, seen, f"single_{reg:04x}_{value:02x}", [(reg, value)])

  if profile in ("all", "combos", "pll"):
    for pclk in [0x0a, 0x0e, 0x15, 0x23]:
      add_case(cases, seen, f"comma_pll_pclk_{pclk:02x}", comma_pll + [(0x4837, pclk)])
    for div in [0x01, 0x11, 0x21, 0x31]:
      add_case(cases, seen, f"mipi_helper_div_{div:02x}", mipi_helper + [(0x3108, div)])
    for pll35 in [0x12, 0x41, 0x5b, 0xb6]:
      for pll36 in [0x30, 0x60]:
        add_case(cases, seen, f"pll_helper_{pll35:02x}_{pll36:02x}", [
          (0x3034, 0x18), (0x3035, pll35), (0x3036, pll36), (0x3037, 0x13), (0x3108, 0x01)
        ])

  if profile in ("all", "combos", "mipi"):
    for ctrl in [0x00, 0x24]:
      for mipi in [0x00, 0x45, 0x58]:
        add_case(cases, seen, f"mipi_ctrl_{ctrl:02x}_300e_{mipi:02x}", [(0x4800, ctrl), (0x300e, mipi)])
    for lane12 in [0x06, 0x04, 0x02, 0x00]:
      for lane13 in [0x02, 0x00, 0x01, 0x03]:
        add_case(cases, seen, f"lane_{lane12:02x}_{lane13:02x}", [(0x3012, lane12), (0x3013, lane13)])

  if profile in ("all", "combos"):
    pll_profiles = [
      ("basepll", []),
      ("mipipll_12_30", [(0x3034, 0x18), (0x3035, 0x12), (0x3036, 0x30), (0x3037, 0x13), (0x3108, 0x01)]),
      ("dvppll_41_60", [(0x3034, 0x18), (0x3035, 0x41), (0x3036, 0x60), (0x3037, 0x13), (0x3108, 0x01)]),
      ("pclk7", [(0x3036, 0x38), (0x3037, 0x16)]),
      ("pclk8", [(0x3036, 0x40), (0x3037, 0x16)]),
      ("pclk9", [(0x3036, 0x60), (0x3037, 0x18)]),
      ("pclk12", [(0x3036, 0x60), (0x3037, 0x16)]),
      ("pclk24", [(0x3036, 0x60), (0x3037, 0x13)]),
      ("pclk48", [(0x3036, 0x60), (0x3037, 0x03)]),
      ("comma_pll", comma_pll),
    ]
    mipi_profiles = [
      ("basemipi", []),
      ("ct_mipi_io", camthink_mipi_io),
      ("comma_mipi", comma_mipi),
      ("ct_ctrl24_lane0602", camthink_mipi_io + [(0x3012, 0x06), (0x3013, 0x02)]),
      ("ctrl00_lane0602", [(0x4800, 0x00), (0x300e, 0x00), (0x3012, 0x06), (0x3013, 0x02)]),
      ("ctrl24_lane0400", camthink_mipi_io + [(0x3012, 0x04), (0x3013, 0x00)]),
      ("ctrl24_lane0402", camthink_mipi_io + [(0x3012, 0x04), (0x3013, 0x02)]),
      ("ctrl24_lane0200", camthink_mipi_io + [(0x3012, 0x02), (0x3013, 0x00)]),
      ("ctrl24_lane0000", camthink_mipi_io + [(0x3012, 0x00), (0x3013, 0x00)]),
      ("dvp_pad", [(0x3017, 0xff), (0x3018, 0xf3), (0x302e, 0x00), (0x300e, 0x58)]),
    ]
    for pll_name, pll_values in pll_profiles:
      for mipi_name, mipi_values in mipi_profiles:
        if not pll_values and not mipi_values:
          continue
        add_case(cases, seen, f"matrix_{pll_name}_{mipi_name}", pll_values + mipi_values)

  return cases


def post_start_alive(bus: int, addr: int) -> tuple[bool, dict[int, int | None]]:
  regs = [0x0100, *ID_REGS, 0x3012, 0x3013, 0x3021, 0x4803, 0x480e, 0x4837]
  values = read_many(bus, addr, regs)
  high = values.get(0x300a)
  low = values.get(0x300b)
  repeated = len({v for v in values.values() if v is not None}) == 1
  alive = high == 0x53 and low == 0x04 and not repeated
  return alive, values


def print_values(label: str, values: dict[int, int | None]) -> None:
  rendered = " ".join(f"{reg:04x}={fmt_value(value)}" for reg, value in values.items())
  print(f"{label}: {rendered}", flush=True)


def run_rdi(args: argparse.Namespace, index: int, candidate: Candidate, regs: list[tuple[int | None, int]]) -> int:
  reg_path = Path(f"/tmp/os04c10_sweep_{index:04d}_{candidate.name}.regs")
  reg_path.write_text(regs_to_text(regs))
  out_path = Path(f"/tmp/os04c10_sweep_{index:04d}_{candidate.name}.raw")
  cmd = [
    args.rdi_probe,
    f"--{args.camera}",
    "--raw10",
    "--init-reg-file", str(reg_path),
    "--frames", "1",
    "--polls", str(args.rdi_polls),
    "--poll-ms", str(args.rdi_poll_ms),
    "--skip-stream-readback",
    "--out", str(out_path),
  ]
  print(f"rdi_cmd: {' '.join(cmd)}", flush=True)
  result = subprocess.run(cmd, text=True)
  print(f"rdi_rc={result.returncode} out={out_path}", flush=True)
  return result.returncode


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--camera", choices=sorted(CAMERAS), default="cam2")
  parser.add_argument("--profile", choices=("all", "single", "pll", "mipi", "combos"), default="all")
  parser.add_argument("--start-delay-ms", type=float, default=80.0)
  parser.add_argument("--reset-delay-ms", type=float, default=5.0)
  parser.add_argument("--write-method", choices=("i2c", "ioctl"), default="i2c")
  parser.add_argument("--limit", type=int, help="maximum candidate count to run")
  parser.add_argument("--start-index", type=int, default=0, help="candidate index to start from")
  parser.add_argument("--stop-on-pass", action="store_true")
  parser.add_argument("--run-rdi", action="store_true")
  parser.add_argument("--rdi-probe", default="/tmp/camss_rdi_probe")
  parser.add_argument("--rdi-polls", type=int, default=25)
  parser.add_argument("--rdi-poll-ms", type=int, default=100)
  parser.add_argument("--list", action="store_true")
  args = parser.parse_args()

  cases = build_candidates(args.profile)
  if args.limit is not None:
    cases = cases[:args.limit]

  print(f"candidate_count={len(cases)} profile={args.profile}", flush=True)
  if args.list:
    for i, case in enumerate(cases):
      rendered = ",".join(f"{reg:04x}={value:02x}" for reg, value in case.overrides)
      print(f"{i:04d} {case.name} {rendered}")
    return 0

  cam = dict(CAMERAS[args.camera])
  bus = int(cam["bus"])
  addr = int(cam["addr"])
  dev = str(cam["dev"])
  subdev = find_subdev(str(cam["name"]))
  passes: list[tuple[int, Candidate]] = []

  for index, case in enumerate(cases):
    if index < args.start_index:
      continue

    print(f"\n=== candidate {index:04d}/{len(cases)-1:04d} {case.name} ===", flush=True)
    print("overrides:", " ".join(f"{reg:04x}=0x{value:02x}" for reg, value in case.overrides) or "(none)", flush=True)

    try:
      rebind(dev)
      force_subdev_power(cam, subdev)
      id_before = read_many(bus, addr, ID_REGS)
      print_values("id_before", id_before)
      if id_before.get(0x300a) != 0x53 or id_before.get(0x300b) != 0x04:
        print("skip: bad id before init", flush=True)
        continue

      regs = list(CAMTHINK_REGS) + list(case.overrides)
      write_sequence("candidate_init", regs, args.write_method, subdev, bus, addr, args.reset_delay_ms)

      expected = expected_subset(final_expected(regs), "key")
      init_values = read_many(bus, addr, list(expected.keys()))
      init_mismatches = compare_values("init_compare", init_values, expected, 8)
      if init_mismatches:
        print("skip: init mismatch", flush=True)
        continue

      write_one(args.write_method, subdev, bus, addr, 0x0100, 0x01)
      time.sleep(max(0.0, args.start_delay_ms) / 1000.0)
      alive, start_values = post_start_alive(bus, addr)
      print_values("post_start", start_values)
      print(f"alive_after_start={int(alive)}", flush=True)

      try:
        write_one(args.write_method, subdev, bus, addr, 0x0100, 0x00)
      except Exception as exc:
        print(f"stop_write_failed={exc}", flush=True)

      if alive:
        passes.append((index, case))
        if args.run_rdi:
          rebind(dev)
          force_subdev_power(cam, subdev)
          run_rdi(args, index, case, regs)
        if args.stop_on_pass:
          break
    except Exception as exc:
      print(f"candidate_error={type(exc).__name__}: {exc}", flush=True)
    finally:
      try:
        rebind(dev)
      except Exception as exc:
        print(f"final_rebind_failed={exc}", flush=True)

  print("\n=== summary ===", flush=True)
  if not passes:
    print("passes=none", flush=True)
  else:
    for index, case in passes:
      print(f"pass {index:04d} {case.name}", flush=True)
  return 0 if passes else 2


if __name__ == "__main__":
  try:
    raise SystemExit(main())
  except KeyboardInterrupt:
    print("interrupted", file=sys.stderr)
    raise SystemExit(130)
