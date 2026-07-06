#!/usr/bin/env python3
"""Hold an OS04C10 at the bare stream-enable edge for cam-v1 probing.

Run on the Dragon as root, usually by piping through dragon.py:

  ./dragon.py ssh 'sudo python3 - --camera cam3 --hold 120 --recover' \
    < tools/os04_bare_start_hold.py

The script intentionally does not start CAMSS or camerad. It only uses the
temporary OS04C10 SENSOR_WRITE_REGS ioctl to power the sensor and optionally
write 0x0100. Use --idle-only for a powered, non-streaming rail reference, and
--start-method i2c to bypass SENSOR_WRITE_REGS for the stream-start write.
Use --pre-reg-file for small mode-table subset tests before stream start.
"""

import argparse
import ctypes
import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path


SENSOR_WRITE_REGS = (1 << 30) | (ord("S") << 8) | 1 | (16 << 16)

CAMERAS = {
  "cam1": {"bus": "16", "addr": "0x36", "name": "os04c10 16-0036", "dev": "16-0036"},
  "cam2": {"bus": "18", "addr": "0x36", "name": "os04c10 18-0036", "dev": "18-0036"},
  "cam3": {"bus": "20", "addr": "0x36", "name": "os04c10 20-0036", "dev": "20-0036"},
}

READ_REGS = [
  0x0100, 0x300A, 0x300B, 0x300C, 0x3012, 0x3013, 0x3021,
  0x4803, 0x4809, 0x480A, 0x480C, 0x480E, 0x4837,
  0x380C, 0x380D, 0x380E, 0x380F,
]


class Reg(ctypes.Structure):
  _fields_ = [("addr", ctypes.c_uint16), ("data", ctypes.c_uint16)]


class Cmd(ctypes.Structure):
  _fields_ = [
    ("regs", ctypes.c_uint64),
    ("count", ctypes.c_uint32),
    ("data_width", ctypes.c_uint8),
    ("pad", ctypes.c_uint8 * 3),
  ]


def find_subdev(name: str) -> str:
  for entry in sorted(os.listdir("/sys/class/video4linux")):
    path = f"/sys/class/video4linux/{entry}/name"
    try:
      if open(path).read().strip() == name:
        return f"/dev/{entry}"
    except OSError:
      pass
  raise RuntimeError(f"could not find V4L2 subdev named {name!r}")


def write_reg(subdev: str, reg: int, value: int) -> None:
  regs = (Reg * 1)(Reg(reg, value))
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


def write_reg_i2c(bus: str, addr: str, reg: int, value: int) -> None:
  hi = (reg >> 8) & 0xFF
  lo = reg & 0xFF
  subprocess.check_call(
    ["i2ctransfer", "-f", "-y", bus, f"w3@{addr}", hex(hi), hex(lo), hex(value & 0xFF)],
    stderr=subprocess.STDOUT,
    text=True,
  )


def parse_reg_file(path: str) -> list[tuple[int | None, int]]:
  return parse_reg_file_string(Path(path).read_text())


def parse_reg_file_string(spec: str) -> list[tuple[int | None, int]]:
  regs = []
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
      raise ValueError(f"malformed register override: {raw_line!r}")

    if key.lower() in ("delay", "delay_ms", "msleep", "sleep"):
      regs.append((None, int(value_s, 0)))
    else:
      regs.append((int(key, 0), int(value_s, 0)))
  return regs


def write_pre_regs(subdev: str, cam: dict[str, str], path: str, method: str, limit: int | None) -> None:
  regs = parse_reg_file(path)
  if limit is not None:
    regs = regs[:limit]
  print(f"writing {len(regs)} pre-start entries from {path} using {method}", flush=True)
  for reg, value in regs:
    if reg is None:
      time.sleep(max(0, value) / 1000.0)
    elif method == "i2c":
      write_reg_i2c(cam["bus"], cam["addr"], reg, value)
    else:
      write_reg(subdev, reg, value)


def write_reg_entries(subdev: str, cam: dict[str, str], regs: list[tuple[int | None, int]], method: str, label: str) -> None:
  print(f"writing {len(regs)} {label} entries using {method}", flush=True)
  for reg, value in regs:
    if reg is None:
      time.sleep(max(0, value) / 1000.0)
    elif method == "i2c":
      write_reg_i2c(cam["bus"], cam["addr"], reg, value)
    else:
      write_reg(subdev, reg, value)


def read_reg(bus: str, addr: str, reg: int) -> int:
  hi = (reg >> 8) & 0xFF
  lo = reg & 0xFF
  out = subprocess.check_output(
    ["i2ctransfer", "-f", "-y", bus, f"w2@{addr}", hex(hi), hex(lo), "r1"],
    stderr=subprocess.STDOUT,
    text=True,
  )
  return int(out.strip().split()[0], 16)


def print_readback(label: str, cam: dict[str, str]) -> None:
  vals = []
  for reg in READ_REGS:
    try:
      vals.append(f"{reg:04x}={read_reg(cam['bus'], cam['addr'], reg):02x}")
    except Exception:
      vals.append(f"{reg:04x}=ERR")
  print(label, " ".join(vals), flush=True)


def rebind(dev: str) -> None:
  driver = "/sys/bus/i2c/drivers/os04c10"
  if os.path.exists(f"{driver}/{dev}"):
    with open(f"{driver}/unbind", "w") as f:
      f.write(dev)
    time.sleep(0.5)
  else:
    print(f"{dev} is not currently bound", flush=True)
  with open(f"{driver}/bind", "w") as f:
    f.write(dev)
  time.sleep(0.5)


def power_on(dev: str) -> None:
  try:
    Path(f"/sys/bus/i2c/devices/{dev}/power/control").write_text("on")
  except OSError as exc:
    print(f"power/control write failed for {dev}: {exc}", flush=True)


def write_one(subdev: str | None, cam: dict[str, str], method: str, reg: int, value: int) -> None:
  if method == "i2c":
    write_reg_i2c(cam["bus"], cam["addr"], reg, value)
  else:
    if subdev is None:
      raise RuntimeError("ioctl method requested but no V4L2 subdev is available")
    write_reg(subdev, reg, value)


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--camera", choices=sorted(CAMERAS), default="cam3")
  parser.add_argument("--hold", type=float, default=120.0, help="seconds to hold after 0x0100=1")
  parser.add_argument("--idle-only", action="store_true", help="hold powered but do not write 0x0100=1")
  parser.add_argument("--control-method", choices=("ioctl", "i2c"), default="ioctl")
  parser.add_argument("--start-method", choices=("ioctl", "i2c"), default="ioctl")
  parser.add_argument("--pre-reg-file", help="optional addr=value register file to write before start")
  parser.add_argument("--pre-reg-method", choices=("ioctl", "i2c"), default="ioctl")
  parser.add_argument("--pre-reg-limit", type=int, help="write only the first N pre-start entries")
  parser.add_argument("--pre-override", action="append", default=[], help="extra pre-start addr=value override")
  parser.add_argument("--read-after-stop", action="store_true", help="read sampled registers after writing 0x0100=0")
  parser.add_argument("--recover", action="store_true", help="unbind/rebind after the hold")
  args = parser.parse_args()

  cam = CAMERAS[args.camera]
  needs_subdev = (
    args.control_method == "ioctl"
    or (not args.idle_only and args.start_method == "ioctl")
    or args.pre_reg_method == "ioctl"
  )
  subdev = find_subdev(cam["name"]) if needs_subdev else None
  print(f"camera={args.camera} subdev={subdev or 'none'} bus={cam['bus']} dev={cam['dev']}", flush=True)

  power_on(cam["dev"])
  write_one(subdev, cam, args.control_method, 0x0100, 0x00)
  time.sleep(0.1)
  print_readback("before", cam)

  if args.pre_reg_file:
    write_pre_regs(subdev, cam, args.pre_reg_file, args.pre_reg_method, args.pre_reg_limit)
    time.sleep(0.1)
    print_readback("after_pre_regs", cam)

  if args.pre_override:
    regs = []
    for spec in args.pre_override:
      regs.extend(parse_reg_file_string(spec))
    write_reg_entries(subdev, cam, regs, args.pre_reg_method, "pre-override")
    time.sleep(0.1)
    print_readback("after_pre_overrides", cam)

  if not args.idle_only:
    write_one(subdev, cam, args.start_method, 0x0100, 0x01)
    time.sleep(0.2)
    print_readback(f"after_start_200ms_{args.start_method}", cam)

  state = "powered idle state" if args.idle_only else "bare stream-start state"
  print(f"holding {state} for {args.hold:.1f}s", flush=True)
  time.sleep(max(0.0, args.hold))

  print_readback("after_hold", cam)
  try:
    write_one(subdev, cam, args.control_method, 0x0100, 0x00)
  except Exception as exc:
    print(f"stop write failed: {exc}", flush=True)
  if args.read_after_stop:
    time.sleep(0.2)
    print_readback("after_stop_200ms", cam)

  if args.recover:
    print(f"rebinding {cam['dev']}", flush=True)
    rebind(cam["dev"])

  return 0


if __name__ == "__main__":
  sys.exit(main())
