#!/usr/bin/env python3
"""Standalone OS04C10 bring-up/check/start helper for cam-v1 on Dragon Q6A.

This intentionally avoids openpilot/camerad. It uses the OS04C10 kernel subdev
only to keep the sensor powered/reset correctly, then uses direct I2C reads and
writes to apply the CamThink NE301/NE302 OS04C10 2688x1520 RAW10 two-lane
register table, compare readback against expected values, and finally write
0x0100=1.

Typical Dragon run:

  sudo python3 os04c10_camthink_bringup.py --camera cam3 --hold 10 --recover

Useful measurement run that holds the started/bad state:

  sudo python3 os04c10_camthink_bringup.py --camera cam3 --hold 120 --no-stop

The register table is extracted from:
  camthink-ai/ne301 and camthink-ai/ne302
  Middlewares/ST/STM32_Camera_Middleware/sensors/os04c10/os04c10.c
  static const uint16_t OS04C10_Common[][2]
"""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import os
import subprocess
import sys
import time
from collections import OrderedDict
from pathlib import Path


SENSOR_WRITE_REGS = (1 << 30) | (ord("S") << 8) | 1 | (16 << 16)

OS04C10_ID_HIGH = 0x53
OS04C10_ID_LOW = 0x04
OS04C10_REVISION = 0x43

CAMERAS = {
  "cam1": {"bus": 16, "addr": 0x36, "name": "os04c10 16-0036", "dev": "16-0036"},
  "cam2": {"bus": 18, "addr": 0x36, "name": "os04c10 18-0036", "dev": "18-0036"},
  "cam3": {"bus": 20, "addr": 0x36, "name": "os04c10 20-0036", "dev": "20-0036"},
}

KEY_REGS = [
  0x0100,
  0x300a, 0x300b, 0x300c,
  0x0301, 0x0303, 0x0305, 0x0307,
  0x3012, 0x3013, 0x3016, 0x3021,
  0x3501, 0x3502, 0x3503,
  0x3808, 0x3809, 0x380a, 0x380b,
  0x380c, 0x380d, 0x380e, 0x380f,
  0x4300, 0x4302, 0x4305,
  0x4803, 0x4809, 0x480a, 0x480c, 0x480e, 0x4837,
  0x5000,
]

# Soft-reset register is write-only/edge-triggered on many OmniVision parts, so
# it is useful in the write sequence but not a strict post-init readback check.
READBACK_SKIP = {0x0103}

# CamThink NE301/NE302 OS04C10_Common table: 2688x1520, RAW10, two data lanes.
CAMTHINK_REGS = (
    (0x0103, 0x01),
    (0x0301, 0x84),
    (0x0303, 0x01),
    (0x0305, 0x5b),
    (0x0306, 0x00),
    (0x0307, 0x17),
    (0x0323, 0x04),
    (0x0324, 0x01),
    (0x0325, 0x62),
    (0x3012, 0x06),
    (0x3013, 0x02),
    (0x3016, 0x32),
    (0x3021, 0x03),
    (0x3106, 0x25),
    (0x3107, 0xa1),
    (0x3500, 0x00),
    (0x3501, 0x04),
    (0x3502, 0x40),
    (0x3503, 0x88),
    (0x3508, 0x00),
    (0x3509, 0x80),
    (0x350a, 0x04),
    (0x350b, 0x00),
    (0x350c, 0x00),
    (0x350d, 0x80),
    (0x350e, 0x04),
    (0x350f, 0x00),
    (0x3510, 0x00),
    (0x3511, 0x01),
    (0x3512, 0x20),
    (0x3624, 0x02),
    (0x3625, 0x4c),
    (0x3660, 0x00),
    (0x3666, 0xa5),
    (0x3667, 0xa5),
    (0x366a, 0x64),
    (0x3673, 0x0d),
    (0x3672, 0x0d),
    (0x3671, 0x0d),
    (0x3670, 0x0d),
    (0x3685, 0x00),
    (0x3694, 0x0d),
    (0x3693, 0x0d),
    (0x3692, 0x0d),
    (0x3691, 0x0d),
    (0x3696, 0x4c),
    (0x3697, 0x4c),
    (0x3698, 0x40),
    (0x3699, 0x80),
    (0x369a, 0x18),
    (0x369b, 0x1f),
    (0x369c, 0x14),
    (0x369d, 0x80),
    (0x369e, 0x40),
    (0x369f, 0x21),
    (0x36a0, 0x12),
    (0x36a1, 0x5d),
    (0x36a2, 0x66),
    (0x370a, 0x00),
    (0x370e, 0x0c),
    (0x3710, 0x00),
    (0x3713, 0x00),
    (0x3725, 0x02),
    (0x372a, 0x03),
    (0x3738, 0xce),
    (0x3748, 0x00),
    (0x374a, 0x00),
    (0x374c, 0x00),
    (0x374e, 0x00),
    (0x3756, 0x00),
    (0x3757, 0x0e),
    (0x3767, 0x00),
    (0x3771, 0x00),
    (0x377b, 0x20),
    (0x377c, 0x00),
    (0x377d, 0x0c),
    (0x3781, 0x03),
    (0x3782, 0x00),
    (0x3789, 0x14),
    (0x3795, 0x02),
    (0x379c, 0x00),
    (0x379d, 0x00),
    (0x37b8, 0x04),
    (0x37ba, 0x03),
    (0x37bb, 0x00),
    (0x37bc, 0x04),
    (0x37be, 0x08),
    (0x37c4, 0x11),
    (0x37c5, 0x80),
    (0x37c6, 0x14),
    (0x37c7, 0x08),
    (0x37da, 0x11),
    (0x381f, 0x08),
    (0x3829, 0x03),
    (0x3881, 0x00),
    (0x3888, 0x04),
    (0x388b, 0x00),
    (0x3c80, 0x10),
    (0x3c86, 0x00),
    (0x3c8c, 0x20),
    (0x3c9f, 0x01),
    (0x3d85, 0x1b),
    (0x3d8c, 0x71),
    (0x3d8d, 0xe2),
    (0x3f00, 0x0b),
    (0x3f06, 0x04),
    (0x400a, 0x01),
    (0x400b, 0x50),
    (0x400e, 0x08),
    (0x4043, 0x7e),
    (0x4045, 0x7e),
    (0x4047, 0x7e),
    (0x4049, 0x7e),
    (0x4090, 0x14),
    (0x40b0, 0x00),
    (0x40b1, 0x00),
    (0x40b2, 0x00),
    (0x40b3, 0x00),
    (0x40b4, 0x00),
    (0x40b5, 0x00),
    (0x40b7, 0x00),
    (0x40b8, 0x00),
    (0x40b9, 0x00),
    (0x40ba, 0x00),
    (0x4301, 0x00),
    (0x4303, 0x00),
    (0x4502, 0x04),
    (0x4503, 0x00),
    (0x4504, 0x06),
    (0x4506, 0x00),
    (0x4507, 0x64),
    (0x4803, 0x10),
    (0x480c, 0x32),
    (0x480e, 0x00),
    (0x4813, 0x00),
    (0x4819, 0x70),
    (0x481f, 0x30),
    (0x4823, 0x3c),
    (0x4825, 0x32),
    (0x4833, 0x10),
    (0x484b, 0x07),
    (0x488b, 0x00),
    (0x4d00, 0x04),
    (0x4d01, 0xad),
    (0x4d02, 0xbc),
    (0x4d03, 0xa1),
    (0x4d04, 0x1f),
    (0x4d05, 0x4c),
    (0x4d0b, 0x01),
    (0x4e00, 0x2a),
    (0x4e0d, 0x00),
    (0x5001, 0x09),
    (0x5004, 0x00),
    (0x5080, 0x04),
    (0x5036, 0x00),
    (0x5180, 0x70),
    (0x5181, 0x10),
    (0x520a, 0x03),
    (0x520b, 0x06),
    (0x520c, 0x0c),
    (0x580b, 0x0f),
    (0x580d, 0x00),
    (0x580f, 0x00),
    (0x5820, 0x00),
    (0x5821, 0x00),
    (0x301c, 0xf0),
    (0x301e, 0xb4),
    (0x301f, 0xd0),
    (0x3022, 0x01),
    (0x3109, 0xe7),
    (0x3600, 0x00),
    (0x3610, 0x65),
    (0x3611, 0x85),
    (0x3613, 0x3a),
    (0x3615, 0x60),
    (0x3621, 0x90),
    (0x3620, 0x0c),
    (0x3629, 0x00),
    (0x3661, 0x04),
    (0x3664, 0x70),
    (0x3665, 0x00),
    (0x3681, 0xa6),
    (0x3682, 0x53),
    (0x3683, 0x2a),
    (0x3684, 0x15),
    (0x3700, 0x2a),
    (0x3701, 0x12),
    (0x3703, 0x28),
    (0x3704, 0x0e),
    (0x3706, 0x4a),
    (0x3709, 0x4a),
    (0x370b, 0xa2),
    (0x370c, 0x01),
    (0x370f, 0x04),
    (0x3714, 0x24),
    (0x3716, 0x24),
    (0x3719, 0x11),
    (0x371a, 0x1e),
    (0x3720, 0x00),
    (0x3724, 0x13),
    (0x373f, 0xb0),
    (0x3741, 0x4a),
    (0x3743, 0x4a),
    (0x3745, 0x4a),
    (0x3747, 0x4a),
    (0x3749, 0xa2),
    (0x374b, 0xa2),
    (0x374d, 0xa2),
    (0x374f, 0xa2),
    (0x3755, 0x10),
    (0x376c, 0x00),
    (0x378d, 0x30),
    (0x3790, 0x4a),
    (0x3791, 0xa2),
    (0x3798, 0xc0),
    (0x379e, 0x00),
    (0x379f, 0x04),
    (0x37a1, 0x01),
    (0x37a2, 0x1e),
    (0x37a8, 0x01),
    (0x37a9, 0x1e),
    (0x37ac, 0xa0),
    (0x37b9, 0x01),
    (0x37bd, 0x01),
    (0x37bf, 0x26),
    (0x37c0, 0x11),
    (0x37c2, 0x04),
    (0x37cd, 0x19),
    (0x37e0, 0x08),
    (0x37e6, 0x04),
    (0x37e5, 0x02),
    (0x37e1, 0x0c),
    (0x3737, 0x04),
    (0x37d8, 0x02),
    (0x37e2, 0x10),
    (0x3739, 0x10),
    (0x3662, 0x10),
    (0x37e4, 0x20),
    (0x37e3, 0x08),
    (0x37d9, 0x08),
    (0x4040, 0x00),
    (0x4041, 0x07),
    (0x4008, 0x02),
    (0x4009, 0x0d),
    (0x3800, 0x00),
    (0x3801, 0x00),
    (0x3802, 0x00),
    (0x3803, 0x00),
    (0x3804, 0x0a),
    (0x3805, 0x8f),
    (0x3806, 0x05),
    (0x3807, 0xff),
    (0x3808, 0x0a),
    (0x3809, 0x80),
    (0x380a, 0x05),
    (0x380b, 0xf0),
    (0x380c, 0x04),
    (0x380d, 0x2e),
    (0x380e, 0x0c),
    (0x380f, 0x4e),
    (0x3811, 0x09),
    (0x3813, 0x09),
    (0x3814, 0x01),
    (0x3815, 0x01),
    (0x3816, 0x01),
    (0x3817, 0x01),
    (0x3820, 0x88),
    (0x3821, 0x00),
    (0x3880, 0x25),
    (0x3882, 0x20),
    (0x3c91, 0x0b),
    (0x3c94, 0x45),
    (0x4000, 0xf3),
    (0x4001, 0x60),
    (0x4003, 0x40),
    (0x4300, 0xff),
    (0x4302, 0x0f),
    (0x4305, 0x83),
    (0x4505, 0x84),
    (0x4809, 0x1e),
    (0x480a, 0x04),
    (0x4837, 0x0a),
    (0x4c00, 0x08),
    (0x4c01, 0x00),
    (0x4c04, 0x00),
    (0x4c05, 0x00),
    (0x5000, 0xf9),
    (0x3624, 0x00),
    (0x3822, 0x14),
    (0x0100, 0x00),
)


class Reg(ctypes.Structure):
  _fields_ = [("addr", ctypes.c_uint16), ("data", ctypes.c_uint16)]


class Cmd(ctypes.Structure):
  _fields_ = [
    ("regs", ctypes.c_uint64),
    ("count", ctypes.c_uint32),
    ("data_width", ctypes.c_uint8),
    ("pad", ctypes.c_uint8 * 3),
  ]


def parse_reg_spec(spec: str) -> list[tuple[int | None, int]]:
  regs: list[tuple[int | None, int]] = []
  for raw_line in spec.replace(",", "\n").replace(";", "\n").splitlines():
    line = raw_line.split("#", 1)[0].strip()
    if not line:
      continue
    if "=" in line:
      key, value_s = [part.strip() for part in line.split("=", 1)]
    elif ":" in line:
      key, value_s = [part.strip() for part in line.split(":", 1)]
    elif len(line.split()) == 2:
      key, value_s = line.split()
    else:
      raise ValueError(f"malformed register spec: {raw_line!r}")

    if key.lower() in ("delay", "delay_ms", "sleep", "msleep"):
      regs.append((None, int(value_s, 0)))
    else:
      regs.append((int(key, 0), int(value_s, 0)))
  return regs


def final_expected(regs: list[tuple[int | None, int]]) -> OrderedDict[int, int]:
  expected: OrderedDict[int, int] = OrderedDict()
  expected[0x300a] = OS04C10_ID_HIGH
  expected[0x300b] = OS04C10_ID_LOW
  expected[0x300c] = OS04C10_REVISION
  for reg, value in regs:
    if reg is not None and reg not in READBACK_SKIP:
      expected[reg] = value & 0xff
  return expected


def expected_subset(expected: OrderedDict[int, int], kind: str, start_value: int | None = None) -> OrderedDict[int, int]:
  if kind == "none":
    return OrderedDict()
  if kind == "all":
    subset = OrderedDict(expected)
  elif kind == "key":
    subset = OrderedDict((reg, expected[reg]) for reg in KEY_REGS if reg in expected)
  else:
    raise ValueError(kind)
  if start_value is not None:
    subset[0x0100] = start_value & 0xff
  return subset


def find_subdev(name: str) -> str:
  for entry in sorted(os.listdir("/sys/class/video4linux")):
    path = f"/sys/class/video4linux/{entry}/name"
    try:
      if Path(path).read_text().strip() == name:
        return f"/dev/{entry}"
    except OSError:
      pass
  raise RuntimeError(f"could not find V4L2 subdev named {name!r}")


def write_reg_ioctl(subdev: str, reg: int, value: int) -> None:
  regs = (Reg * 1)(Reg(reg, value & 0xff))
  cmd = Cmd(
    ctypes.addressof(regs),
    1,
    1,
    (ctypes.c_uint8 * 3)(0, 0, 0),
  )
  fd = os.open(subdev, os.O_RDWR)
  try:
    fcntl.ioctl(fd, SENSOR_WRITE_REGS, ctypes.string_at(ctypes.addressof(cmd), ctypes.sizeof(cmd)))
  finally:
    os.close(fd)


def write_reg_i2c(bus: int, addr: int, reg: int, value: int) -> None:
  hi = (reg >> 8) & 0xff
  lo = reg & 0xff
  subprocess.check_call(
    ["i2ctransfer", "-f", "-y", str(bus), f"w3@0x{addr:02x}", f"0x{hi:02x}", f"0x{lo:02x}", f"0x{value & 0xff:02x}"],
    stderr=subprocess.STDOUT,
    text=True,
  )


def read_reg_i2c(bus: int, addr: int, reg: int) -> int:
  hi = (reg >> 8) & 0xff
  lo = reg & 0xff
  out = subprocess.check_output(
    ["i2ctransfer", "-f", "-y", str(bus), f"w2@0x{addr:02x}", f"0x{hi:02x}", f"0x{lo:02x}", "r1"],
    stderr=subprocess.STDOUT,
    text=True,
  )
  return int(out.strip().split()[0], 16)


def write_one(method: str, subdev: str | None, bus: int, addr: int, reg: int, value: int) -> None:
  if method == "i2c":
    write_reg_i2c(bus, addr, reg, value)
  elif method == "ioctl":
    if subdev is None:
      raise RuntimeError("ioctl write method requires a V4L2 subdev")
    write_reg_ioctl(subdev, reg, value)
  else:
    raise ValueError(method)


def write_sequence(
  label: str,
  regs: list[tuple[int | None, int]],
  method: str,
  subdev: str | None,
  bus: int,
  addr: int,
  reset_delay_ms: float,
) -> None:
  print(f"{label}: writing {len(regs)} entries using {method}", flush=True)
  for index, (reg, value) in enumerate(regs, start=1):
    if reg is None:
      print(f"  delay {value} ms", flush=True)
      time.sleep(max(0, value) / 1000.0)
      continue

    write_one(method, subdev, bus, addr, reg, value)
    if reg == 0x0103 and reset_delay_ms > 0:
      print(f"  soft reset at entry {index}; delay {reset_delay_ms:g} ms", flush=True)
      time.sleep(reset_delay_ms / 1000.0)


def read_many(bus: int, addr: int, regs: list[int]) -> OrderedDict[int, int | None]:
  values: OrderedDict[int, int | None] = OrderedDict()
  for reg in regs:
    try:
      values[reg] = read_reg_i2c(bus, addr, reg)
    except Exception:
      values[reg] = None
  return values


def fmt_value(value: int | None) -> str:
  if value is None:
    return "ERR"
  return f"0x{value:02x}"


def print_snapshot(label: str, values: OrderedDict[int, int | None]) -> None:
  rendered = " ".join(f"{reg:04x}={fmt_value(value)}" for reg, value in values.items())
  print(f"{label}: {rendered}", flush=True)


def compare_values(
  label: str,
  actual: OrderedDict[int, int | None],
  expected: OrderedDict[int, int],
  max_mismatches: int,
) -> int:
  mismatches = []
  for reg, want in expected.items():
    got = actual.get(reg)
    if got != want:
      mismatches.append((reg, want, got))

  matched = len(expected) - len(mismatches)
  print(f"{label}: {matched}/{len(expected)} matched", flush=True)
  for reg, want, got in mismatches[:max_mismatches]:
    print(f"  mismatch {reg:04x}: expected 0x{want:02x}, got {fmt_value(got)}", flush=True)
  if len(mismatches) > max_mismatches:
    print(f"  ... {len(mismatches) - max_mismatches} more mismatches suppressed", flush=True)
  return len(mismatches)


def read_chip_id(bus: int, addr: int, label: str) -> bool:
  regs = [0x300a, 0x300b, 0x300c]
  values = read_many(bus, addr, regs)
  print_snapshot(label, values)
  high, low, rev = [values.get(reg) for reg in regs]
  ok = high == OS04C10_ID_HIGH and low == OS04C10_ID_LOW
  if ok:
    print(f"{label}: OS04C10 id=0x{high:02x}{low:02x} revision={fmt_value(rev)}", flush=True)
  else:
    print(f"{label}: invalid OS04C10 id high={fmt_value(high)} low={fmt_value(low)} revision={fmt_value(rev)}", flush=True)
  return ok


def force_subdev_power(cam: dict[str, object], subdev: str) -> None:
  dev = str(cam["dev"])
  power_control = Path(f"/sys/bus/i2c/devices/{dev}/power/control")
  try:
    power_control.write_text("on")
    print(f"runtime PM forced on via {power_control}", flush=True)
  except OSError as exc:
    print(f"warning: could not write {power_control}: {exc}", flush=True)

  # The temporary SENSOR_WRITE_REGS ioctl resumes the sensor through the kernel
  # power path, which also applies reset GPIO sequencing from the OS04C10 driver.
  write_reg_ioctl(subdev, 0x0100, 0x00)
  time.sleep(0.1)


def rebind(dev: str) -> None:
  driver = Path("/sys/bus/i2c/drivers/os04c10")
  if (driver / dev).exists():
    (driver / "unbind").write_text(dev)
    time.sleep(0.5)
  else:
    print(f"{dev} is not currently bound; trying bind anyway", flush=True)
  (driver / "bind").write_text(dev)
  time.sleep(0.5)


def choose_camera(args: argparse.Namespace) -> dict[str, object]:
  cam = dict(CAMERAS[args.camera])
  if args.bus is not None:
    cam["bus"] = args.bus
  if args.addr is not None:
    cam["addr"] = args.addr
  if args.subdev_name is not None:
    cam["name"] = args.subdev_name
  if args.i2c_dev is not None:
    cam["dev"] = args.i2c_dev
  return cam


def self_test() -> int:
  regs = list(CAMTHINK_REGS)
  expected = final_expected(regs)
  checks = {
    "table_writes": len(regs) == 290,
    "unique_expected": len(expected) == 291,  # 289 table regs except 0103, plus ID/revision.
    "id_high": expected[0x300a] == 0x53,
    "id_low": expected[0x300b] == 0x04,
    "stream_off_final": expected[0x0100] == 0x00,
    "two_lane_3012": expected[0x3012] == 0x06,
    "two_lane_3013": expected[0x3013] == 0x02,
    "raw10_4305": expected[0x4305] == 0x83,
    "width": expected[0x3808] == 0x0a and expected[0x3809] == 0x80,
    "height": expected[0x380a] == 0x05 and expected[0x380b] == 0xf0,
    "hts": expected[0x380c] == 0x04 and expected[0x380d] == 0x2e,
    "vts": expected[0x380e] == 0x0c and expected[0x380f] == 0x4e,
    "mipi_timing": expected[0x4837] == 0x0a,
  }
  for name, ok in checks.items():
    print(f"{name}: {'ok' if ok else 'FAIL'}")
  return 0 if all(checks.values()) else 1


def dump_regs(regs: list[tuple[int | None, int]]) -> None:
  for reg, value in regs:
    if reg is None:
      print(f"delay={value}")
    else:
      print(f"0x{reg:04x}=0x{value & 0xff:02x}")


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--self-test", action="store_true", help="validate embedded table without touching hardware")
  parser.add_argument("--dump-regs", action="store_true", help="print final register sequence as addr=value and exit")
  parser.add_argument("--camera", choices=sorted(CAMERAS), default="cam3")
  parser.add_argument("--bus", type=int, help="override I2C bus number")
  parser.add_argument("--addr", type=lambda s: int(s, 0), help="override 7-bit I2C address")
  parser.add_argument("--subdev-name", help="override V4L2 subdev name used for power")
  parser.add_argument("--i2c-dev", help="override kernel I2C device id, e.g. 20-0036")
  parser.add_argument("--no-subdev-power", action="store_true", help="skip kernel subdev power/reset setup")
  parser.add_argument("--write-method", choices=("i2c", "ioctl"), default="i2c")
  parser.add_argument("--reset-delay-ms", type=float, default=5.0, help="delay after writing 0x0103")
  parser.add_argument("--camthink-id-reset", action="store_true", help="write 0x3008=0x80 before the initial ID read, as CamThink ReadID does")
  parser.add_argument("--override", action="append", default=[], help="append addr=value overrides after the CamThink table")
  parser.add_argument("--limit", type=int, help="write only the first N entries of the final sequence")
  parser.add_argument("--check", choices=("all", "key", "none"), default="all", help="post-init compare scope")
  parser.add_argument("--post-start-check", choices=("all", "key", "none"), default="key", help="post-start compare scope")
  parser.add_argument("--max-mismatches", type=int, default=80)
  parser.add_argument("--no-start", action="store_true", help="write and verify init table but do not stream-start")
  parser.add_argument("--hold", type=float, default=2.0, help="seconds to hold after stream start")
  parser.add_argument("--no-stop", action="store_true", help="leave sensor started after the hold")
  parser.add_argument("--recover", action="store_true", help="unbind/rebind the kernel sensor driver at the end")
  parser.add_argument("--force", action="store_true", help="continue even if initial ID read is wrong")
  parser.add_argument("--fail-on-init-mismatch", action="store_true")
  args = parser.parse_args()

  if args.self_test:
    return self_test()

  if args.dump_regs:
    sequence: list[tuple[int | None, int]] = list(CAMTHINK_REGS)
    for override in args.override:
      sequence.extend(parse_reg_spec(override))
    if args.limit is not None:
      sequence = sequence[:args.limit]
    dump_regs(sequence)
    return 0

  cam = choose_camera(args)
  bus = int(cam["bus"])
  addr = int(cam["addr"])
  subdev = None

  print(
    "mode: CamThink OS04C10 2688x1520 RAW10 two-lane table, "
    "STM32 DCMIPP PHYBitrate=1600 reference",
    flush=True,
  )
  print(f"target: camera={args.camera} bus={bus} addr=0x{addr:02x} dev={cam['dev']}", flush=True)

  if not args.no_subdev_power:
    subdev = find_subdev(str(cam["name"]))
    print(f"subdev: {subdev} name={cam['name']}", flush=True)
    force_subdev_power(cam, subdev)
  elif args.write_method == "ioctl":
    raise RuntimeError("--write-method ioctl cannot be used with --no-subdev-power")
  else:
    print("subdev power setup skipped; assuming rails/reset are already valid", flush=True)

  if args.camthink_id_reset:
    print("camthink-id-reset: writing 0x3008=0x80 and waiting 5 ms", flush=True)
    write_one(args.write_method, subdev, bus, addr, 0x3008, 0x80)
    time.sleep(0.005)

  initial_id_ok = read_chip_id(bus, addr, "initial_id")
  if not initial_id_ok and not args.force:
    return 1

  idle_regs = expected_subset(final_expected(list(CAMTHINK_REGS)), "key")
  print_snapshot("idle_key_readback", read_many(bus, addr, list(idle_regs.keys())))

  sequence: list[tuple[int | None, int]] = list(CAMTHINK_REGS)
  for override in args.override:
    sequence.extend(parse_reg_spec(override))
  if args.limit is not None:
    sequence = sequence[:args.limit]

  expected = final_expected(sequence)
  write_sequence("camthink_init", sequence, args.write_method, subdev, bus, addr, args.reset_delay_ms)
  time.sleep(0.1)

  init_expected = expected_subset(expected, args.check)
  init_mismatches = 0
  if init_expected:
    init_actual = read_many(bus, addr, list(init_expected.keys()))
    print_snapshot("after_init_readback", init_actual if args.check == "key" else OrderedDict(list(init_actual.items())[:len(KEY_REGS)]))
    init_mismatches = compare_values("after_init_compare", init_actual, init_expected, args.max_mismatches)

  if init_mismatches and args.fail_on_init_mismatch:
    if args.recover and not args.no_subdev_power:
      rebind(str(cam["dev"]))
    return 2

  if args.no_start:
    print("no-start requested; leaving sensor initialized with 0x0100=0", flush=True)
    if args.recover and not args.no_subdev_power:
      rebind(str(cam["dev"]))
    return 0 if init_mismatches == 0 else 2

  print("stream_start: writing 0x0100=0x01", flush=True)
  write_one(args.write_method, subdev, bus, addr, 0x0100, 0x01)
  time.sleep(0.2)

  post_expected = expected_subset(expected, args.post_start_check, start_value=1)
  post_mismatches = 0
  if post_expected:
    post_actual = read_many(bus, addr, list(post_expected.keys()))
    print_snapshot("after_start_200ms_readback", post_actual)
    post_mismatches = compare_values("after_start_200ms_compare", post_actual, post_expected, args.max_mismatches)

  if args.hold > 0:
    print(f"holding started state for {args.hold:.1f}s", flush=True)
    time.sleep(args.hold)
    hold_expected = expected_subset(expected, args.post_start_check, start_value=1)
    if hold_expected:
      hold_actual = read_many(bus, addr, list(hold_expected.keys()))
      print_snapshot("after_hold_readback", hold_actual)
      compare_values("after_hold_compare", hold_actual, hold_expected, args.max_mismatches)

  if not args.no_stop:
    print("stream_stop: writing 0x0100=0x00", flush=True)
    try:
      write_one(args.write_method, subdev, bus, addr, 0x0100, 0x00)
      time.sleep(0.2)
      print_snapshot("after_stop_readback", read_many(bus, addr, KEY_REGS))
    except Exception as exc:
      print(f"stream_stop failed: {exc}", flush=True)

  if args.recover:
    if args.no_subdev_power:
      print("recover skipped because --no-subdev-power was used", flush=True)
    else:
      print(f"recover: rebinding {cam['dev']}", flush=True)
      rebind(str(cam["dev"]))

  return 0 if init_mismatches == 0 and post_mismatches == 0 else 3


if __name__ == "__main__":
  try:
    sys.exit(main())
  except KeyboardInterrupt:
    print("interrupted", file=sys.stderr)
    sys.exit(130)
