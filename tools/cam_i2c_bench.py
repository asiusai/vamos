#!/usr/bin/env python3
"""Dragon camera I2C/LED bench checks.

Run on the Dragon, usually from the host as:

  ./dragon.py ssh 'sudo python3 - --gpio-compare' < tools/cam_i2c_bench.py
  ./dragon.py ssh 'sudo python3 - --white' < tools/cam_i2c_bench.py

The script intentionally uses i2ctransfer so it tests the same user-visible
paths used during bring-up.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass


SENSOR_ADDR = 0x36
LED_ADDR = 0x64


@dataclass(frozen=True)
class Camera:
  name: str
  bus: int
  dev: str


CAMERAS = (
  Camera("cam1", 16, "16-0036"),
  Camera("cam2", 18, "18-0036"),
  Camera("cam3", 20, "20-0036"),
)


CCI1_DEV = "ac4b000.cci"
CCI1_DRIVER = "/sys/bus/platform/drivers/i2c-qcom-cci"

# Linux GPIO numbers for TLMM GPIO73..76. CAM2 is the control; CAM3 is suspect.
GPIO_COMPARE = (
  ("cam2-scl-gpio73", 620),
  ("cam2-sda-gpio74", 621),
  ("cam3-scl-gpio75", 622),
  ("cam3-sda-gpio76", 623),
)


def run(cmd: list[str], timeout: float = 3.0) -> subprocess.CompletedProcess[str]:
  return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)


def write(path: str, value: str | int) -> None:
  with open(path, "w", encoding="utf-8") as f:
    f.write(str(value))


def read(path: str) -> str:
  with open(path, encoding="utf-8") as f:
    return f.read().strip()


def i2c_write(bus: int, addr: int, payload: list[int], timeout: float = 1.0) -> bool:
  cmd = ["i2ctransfer", "-f", "-y", str(bus), f"w{len(payload)}@0x{addr:02x}"]
  cmd.extend(f"0x{x:02x}" for x in payload)
  p = run(cmd, timeout=timeout)
  return p.returncode == 0


def sensor_id(bus: int) -> tuple[bool, str]:
  p = run(
    [
      "i2ctransfer",
      "-f",
      "-y",
      str(bus),
      f"w2@0x{SENSOR_ADDR:02x}",
      "0x30",
      "0x0a",
      "r3",
    ],
    timeout=3.0,
  )
  out = p.stdout.strip()
  if p.returncode == 0:
    return True, out
  return False, (p.stderr.strip() or out or f"exit {p.returncode}")


def led_ack(bus: int) -> bool:
  return i2c_write(bus, LED_ADDR, [0xff, 0x00])


def force_active(dev: str) -> str:
  control = f"/sys/bus/i2c/devices/{dev}/power/control"
  runtime = f"/sys/bus/i2c/devices/{dev}/power/runtime_status"
  if not os.path.exists(control):
    return "missing"
  try:
    write(control, "on")
  except OSError as e:
    return f"power-control-error:{e.errno}"
  return read(runtime) if os.path.exists(runtime) else "active?"


def led_white(bus: int) -> bool:
  # IS31FL3199-QFLS2-TR full-current, all channels enabled, all PWM 0xff.
  seq = [
    (0xff, 0x00),
    (0x03, 0x00),
    (0x04, 0x40),
    (0x01, 0x77),
    (0x02, 0x07),
  ]
  seq.extend((reg, 0xff) for reg in range(0x07, 0x10))
  seq.extend([(0x10, 0x00), (0x00, 0x01)])
  ok = True
  for reg, val in seq:
    ok = i2c_write(bus, LED_ADDR, [reg, val]) and ok
  return ok


def led_rgb(bus: int, red: int, green: int, blue: int) -> bool:
  # Three RGB LEDs share 9 outputs: R/G/B repeated.
  values = [red, green, blue] * 3
  ok = True
  for index, val in enumerate(values, start=0x07):
    ok = i2c_write(bus, LED_ADDR, [index, val & 0xff]) and ok
  ok = i2c_write(bus, LED_ADDR, [0x10, 0x00]) and ok
  ok = i2c_write(bus, LED_ADDR, [0x00, 0x01]) and ok
  return ok


def export_gpio(num: int) -> None:
  if os.path.exists(f"/sys/class/gpio/gpio{num}"):
    return
  try:
    write("/sys/class/gpio/export", num)
  except OSError:
    pass
  time.sleep(0.05)


def unexport_gpio(num: int) -> None:
  if not os.path.exists(f"/sys/class/gpio/gpio{num}"):
    return
  try:
    write("/sys/class/gpio/unexport", num)
  except OSError:
    pass


def gpio_direction(num: int, direction: str) -> None:
  write(f"/sys/class/gpio/gpio{num}/direction", direction)


def gpio_value(num: int) -> str:
  return read(f"/sys/class/gpio/gpio{num}/value")


def unbind_cci1() -> None:
  if os.path.exists(f"/sys/bus/platform/devices/{CCI1_DEV}/driver/unbind"):
    try:
      write(f"{CCI1_DRIVER}/unbind", CCI1_DEV)
    except OSError:
      pass
  time.sleep(0.2)


def bind_cci1() -> None:
  try:
    write(f"{CCI1_DRIVER}/bind", CCI1_DEV)
  except OSError:
    pass
  time.sleep(1.0)


def gpio_compare() -> list[str]:
  lines: list[str] = []
  unbind_cci1()
  try:
    for _, gpio in GPIO_COMPARE:
      export_gpio(gpio)

    lines.append("gpio-drive-high:")
    for label, gpio in GPIO_COMPARE:
      gpio_direction(gpio, "out")
      write(f"/sys/class/gpio/gpio{gpio}/value", 1)
      lines.append(f"  {label}: out={gpio_value(gpio)}")

    lines.append("gpio-release-input:")
    for label, gpio in GPIO_COMPARE:
      gpio_direction(gpio, "in")
      lines.append(f"  {label}: in={gpio_value(gpio)}")
  finally:
    for _, gpio in GPIO_COMPARE:
      unexport_gpio(gpio)
    bind_cci1()

  return lines


def print_gpio_summary() -> None:
  p = run(
    [
      "sh",
      "-c",
      "grep -E 'gpio7[3-8]|gpio20|gpio36' /sys/kernel/debug/gpio",
    ],
    timeout=1.0,
  )
  if p.returncode == 0 and p.stdout.strip():
    print("gpio:")
    for line in p.stdout.rstrip().splitlines():
      print(f"  {line}")


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--white", action="store_true", help="set ACKing LEDs full white")
  parser.add_argument("--disco", type=float, default=0.0, metavar="SECONDS",
                      help="cycle ACKing LEDs through RGB colors")
  parser.add_argument("--gpio-compare", action="store_true",
                      help="temporarily unbind CCI1 and compare CAM2/CAM3 GPIO release")
  args = parser.parse_args()

  print("camera-i2c:")
  ack_buses: list[int] = []
  for cam in CAMERAS:
    runtime = force_active(cam.dev)
    sensor_ok, sensor_out = sensor_id(cam.bus)
    led_ok = led_ack(cam.bus)
    if led_ok:
      ack_buses.append(cam.bus)
    sensor_state = "OK" if sensor_ok else "FAIL"
    led_state = "OK" if led_ok else "FAIL"
    print(
      f"  {cam.name} bus{cam.bus}: power={runtime} "
      f"sensor={sensor_state}({sensor_out}) led={led_state}"
    )

  if args.white:
    print("led-white:")
    for bus in ack_buses:
      print(f"  bus{bus}: {'OK' if led_white(bus) else 'FAIL'}")

  if args.disco > 0:
    print("led-disco:")
    colors = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255))
    end = time.monotonic() + args.disco
    step = 0
    while time.monotonic() < end:
      r, g, b = colors[step % len(colors)]
      for bus in ack_buses:
        led_rgb(bus, r, g, b)
      step += 1
      time.sleep(0.25)
    for bus in ack_buses:
      led_white(bus)
    print(f"  buses={ack_buses} steps={step}")

  if args.gpio_compare:
    print("cci1-gpio-compare:")
    for line in gpio_compare():
      print(line)
    # Restore runtime power after the CCI rebind.
    for cam in CAMERAS:
      force_active(cam.dev)

  print_gpio_summary()
  return 0


if __name__ == "__main__":
  sys.exit(main())
