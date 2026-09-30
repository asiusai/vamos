import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import call, patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "userspace/root/usr/lib/vamos/hardware.py"
spec = importlib.util.spec_from_file_location("vamos_hardware", MODULE_PATH)
assert spec is not None and spec.loader is not None
hardware = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hardware)


class HardwarePolicyTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.root = Path(temporary.name)
    for name in ("SYS", "PROC", "DEV"):
      patcher = patch.object(hardware, name, self.root / name.lower())
      patcher.start()
      self.addCleanup(patcher.stop)

  def write(self, path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return path

  def gpu(self):
    control = self.write(hardware.SYS / "devices/platform/soc@0/3d00000.gpu/power/control", "on")
    governor = self.write(hardware.SYS / "class/devfreq/3d00000.gpu/governor", "simple_ondemand")
    frequency = self.write(hardware.SYS / "class/devfreq/3d00000.gpu/userspace/set_freq", "0")
    return control, governor, frequency

  def test_initialize_routes_irqs_and_all_spi_workers(self):
    kmsg = self.write(hardware.DEV / "kmsg", "")
    kmsg.chmod(0o600)
    stats = self.write(hardware.SYS / "kernel/debug/ufshcd/ufs/stats", "")
    stats.chmod(0o600)
    default = self.write(hardware.PROC / "irq/default_smp_affinity", "ff")
    _, governor, frequency = self.gpu()
    affinities = []
    for irq, action in enumerate(("msm_vidc", "i2c_geni,other", "spi_geni", "spi_geni_extra")):
      self.write(hardware.SYS / f"kernel/irq/{irq}/actions", action)
      affinities.append(self.write(hardware.PROC / f"irq/{irq}/smp_affinity_list", "0-7"))

    with patch.object(hardware.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "101\n102\n")) as run:
      hardware.initialize()

    self.assertEqual([p.read_text() for p in affinities], ["1", "1", "3", "0-7"])
    self.assertEqual(default.read_text(), "f")
    self.assertEqual(kmsg.stat().st_mode & 0o777, 0o622)
    self.assertEqual(stats.stat().st_mode & 0o777, 0o644)
    self.assertEqual(governor.read_text(), "userspace")
    self.assertEqual(frequency.read_text(), "812000000")
    self.assertEqual(run.call_args_list[1:], [
      call(["chrt", "-f", "-p", "1", "101"], check=False),
      call(["taskset", "-pc", "3", "101"], check=False),
      call(["chrt", "-f", "-p", "1", "102"], check=False),
      call(["taskset", "-pc", "3", "102"], check=False),
    ])

  def test_gpu_power_save_restores_clock_when_leaving_idle(self):
    control, governor, frequency = self.gpu()
    hardware.set_gpu_power_save(True)
    self.assertEqual(control.read_text(), "auto")
    self.assertEqual(governor.read_text(), "simple_ondemand")
    hardware.set_gpu_power_save(False)
    self.assertEqual(control.read_text(), "on")
    self.assertEqual(governor.read_text(), "userspace")
    self.assertEqual(frequency.read_text(), "812000000")

  def test_thermal_policy_preserves_critical_and_unrelated_trips(self):
    temps = []
    for index, (zone_type, trip_type, temp) in enumerate((
      ("cpu0-thermal", "passive", "90000"),
      ("gpuss0-thermal", "hot", "95000"),
      ("cpu1-thermal", "critical", "100000"),
      ("ufs-thermal", "passive", "90000"),
      ("ddr-thermal", "passive", "85000"),
    )):
      zone = hardware.SYS / f"class/thermal/thermal_zone{index}"
      self.write(zone / "type", zone_type)
      self.write(zone / "trip_point_0_type", trip_type)
      temps.append(self.write(zone / "trip_point_0_temp", temp))
    hardware.raise_thermal_limits()
    self.assertEqual([p.read_text() for p in temps], ["100000", "105000", "100000", "90000", "85000"])

  def panda_pins(self, base):
    root = hardware.SYS / "class/gpio"
    self.write(root / f"gpiochip{base}/label", "f100000.pinctrl\n")
    self.write(root / f"gpiochip{base}/base", str(base))
    for offset in (28, 31):
      self.write(root / f"gpio{base + offset}/direction", "in")
      self.write(root / f"gpio{base + offset}/value", "0")
    return [root / f"gpio{base + offset}/value" for offset in (28, 31)]

  def test_panda_reset_and_rom_entry_with_dynamic_gpio_base(self):
    for base, bootloader in ((547, False), (512, True)):
      with self.subTest(base=base, bootloader=bootloader):
        boot, reset = self.panda_pins(base)
        levels = []
        def delay(_):
          levels.append((boot.read_text(), reset.read_text()))
        with patch.object(hardware, "PANDA_RESET_LOCK", self.root / "panda.lock"), \
             patch.object(hardware.time, "sleep", side_effect=delay):
          hardware.reset_panda(bootloader)
        self.assertEqual(levels, [(str(int(bootloader)), "1"), (str(int(bootloader)), "0")])
        self.assertEqual((boot.read_text(), reset.read_text()), ("0", "0"))
        self.assertFalse((boot.parent.parent / f"gpio{base + 27}").exists())
        # Do not leave two matching chips for the next subtest.
        (boot.parent.parent / f"gpiochip{base}/label").write_text("other-controller")

  def test_panda_recovery_releases_both_pins_on_failure(self):
    boot, reset = self.panda_pins(547)
    with patch.object(hardware, "PANDA_RESET_LOCK", self.root / "panda.lock"), \
         patch.object(hardware.time, "sleep", side_effect=RuntimeError("interrupted")):
      with self.assertRaisesRegex(RuntimeError, "interrupted"):
        hardware.reset_panda(True)
    self.assertEqual((boot.read_text(), reset.read_text()), ("0", "0"))


if __name__ == "__main__":
  unittest.main()
