"""Dragon hardware policy, invoked by the workload when its power state changes."""

import argparse
import fcntl
from pathlib import Path
import subprocess
import time


SYS = Path("/sys")
PROC = Path("/proc")
DEV = Path("/dev")
PANDA_RESET_LOCK = Path("/run/lock/vamos-panda-reset.lock")


def reset_panda(bootloader: bool = False) -> None:
  root = SYS / "class/gpio"
  chip = next(c for c in root.glob("gpiochip*") if (c / "label").read_text().strip() == "f100000.pinctrl")
  base = int((chip / "base").read_text())
  # Panda v6: header pins 29/31 are TLMM 31/28. Both are active high;
  # the board's reset transistor converts GPIO31 high into STM32 NRST low.
  with PANDA_RESET_LOCK.open("w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    pins = []
    for offset in (28, 31):
      pin = root / f"gpio{base + offset}"
      if not pin.exists():
        (root / "export").write_text(str(base + offset))
        deadline = time.monotonic() + 1.
        while not (pin / "direction").exists():
          if time.monotonic() >= deadline:
            raise TimeoutError(f"GPIO{offset} did not export")
          time.sleep(.01)
      (pin / "direction").write_text("low")
      pins.append(pin / "value")
    boot, reset = pins
    try:
      reset.write_text("1")
      boot.write_text("1" if bootloader else "0")
      time.sleep(.02)
      reset.write_text("0")
      time.sleep(.02)
    finally:
      boot.write_text("0")
      reset.write_text("0")


def affine_irq(cpu: int, action: str) -> None:
  for irq in (SYS / "kernel/irq").glob("[0-9]*"):
    try:
      actions = (irq / "actions").read_text().strip().split(",")
    except FileNotFoundError:
      continue
    if action in actions:
      (PROC / f"irq/{irq.name}/smp_affinity_list").write_text(str(cpu))


def set_gpu_power_save(enabled: bool) -> None:
  (SYS / "devices/platform/soc@0/3d00000.gpu/power/control").write_text("auto" if enabled else "on")
  if not enabled:
    set_gpu_clock()


def set_gpu_clock() -> None:
  (SYS / "class/devfreq/3d00000.gpu/governor").write_text("userspace")
  (SYS / "class/devfreq/3d00000.gpu/userspace/set_freq").write_text("812000000")


def raise_thermal_limits() -> None:
  trip_overrides = {90000: 100000, 95000: 105000, 100000: 108000, 105000: 109000}
  for zone in (SYS / "class/thermal").glob("thermal_zone*"):
    try:
      zone_type = (zone / "type").read_text().strip()
    except OSError:
      continue
    if not zone_type.startswith(("cpu", "aoss", "ddr", "video", "cpuss", "gpuss")):
      continue

    for i in range(4):
      temp_path = zone / f"trip_point_{i}_temp"
      try:
        temp = int(temp_path.read_text().strip())
        trip_type = (zone / f"trip_point_{i}_type").read_text().strip()
      except (OSError, ValueError):
        continue
      if trip_type in ("passive", "hot") and temp in trip_overrides:
        temp_path.write_text(str(trip_overrides[temp]))


def initialize() -> None:
  kmsg = DEV / "kmsg"
  kmsg.chmod(kmsg.stat().st_mode | 0o222)
  for stats in (SYS / "kernel/debug/ufshcd").glob("*/stats"):
    stats.chmod(stats.stat().st_mode | 0o444)
  default_affinity = PROC / "irq/default_smp_affinity"
  if default_affinity.exists():
    default_affinity.write_text("f")

  affine_irq(1, "msm_vidc")
  affine_irq(1, "i2c_geni")
  set_gpu_clock()
  raise_thermal_limits()

  affine_irq(3, "spi_geni")
  # pgrep can return more than one SPI worker. Apply the policy to each PID.
  workers = subprocess.run(["pgrep", "-f", "spi0"], capture_output=True, text=True, check=False)
  for pid in workers.stdout.split():
    subprocess.run(["chrt", "-f", "-p", "1", pid], check=False)
    subprocess.run(["taskset", "-pc", "3", pid], check=False)


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest="command", required=True)
  commands.add_parser("initialize")
  panda = commands.add_parser("panda-reset")
  panda.add_argument("--bootloader", action="store_true", help="enter the STM32 ROM bootloader")
  gpu = commands.add_parser("gpu-power-save")
  gpu.add_argument("state", choices=("on", "off"))
  args = parser.parse_args()
  if args.command == "initialize":
    initialize()
  elif args.command == "panda-reset":
    reset_panda(args.bootloader)
  else:
    set_gpu_power_save(args.state == "on")
  return 0
