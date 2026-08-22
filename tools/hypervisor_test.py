#!/usr/bin/env python3

import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "userspace/root/usr/bin/vamos-hypervisor"


class HypervisorHelperTest(unittest.TestCase):
  def setUp(self) -> None:
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)
    self.efivars = self.root / "efivars"
    self.efivars.mkdir()
    self.variable = self.efivars / "HypervisorOverride-e9139283-6a58-402f-b397-4c4671c9e067"
    self.variable.write_bytes(struct.pack("<II", 7, 2))
    self.bios = self.root / "bios_version"
    self.bios.write_text("test-bios\n")
    self.env = os.environ | {
      "VAMOS_KVM_DEVICE": str(self.root / "kvm"),
      "VAMOS_EFIVARS_DIR": str(self.efivars),
      "VAMOS_EFIVARS_MOUNTED": "1",
      "VAMOS_HYPERVISOR_STATE_DIR": str(self.root / "state"),
      "VAMOS_BIOS_VERSION_FILE": str(self.bios),
      "VAMOS_HYPERVISOR_REBOOT_MARKER": str(self.root / "reboot-required"),
    }

  def run_helper(self, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
      [str(HELPER), command],
      env=self.env,
      check=False,
      capture_output=True,
      text=True,
    )

  def test_active_system_does_not_touch_efivars(self) -> None:
    (self.root / "kvm").touch()
    self.variable.unlink()
    result = self.run_helper("auto")
    self.assertEqual(0, result.returncode, result.stderr)
    self.assertIn("active", result.stdout)
    self.assertFalse((self.root / "state").exists())
    self.assertFalse((self.root / "reboot-required").exists())

  def test_auto_arms_exact_one_shot_value(self) -> None:
    result = self.run_helper("auto")
    self.assertEqual(0, result.returncode, result.stderr)
    self.assertEqual(struct.pack("<II", 7, 1), self.variable.read_bytes())
    self.assertEqual("test-bios\n", (self.root / "state/armed-bios-version").read_text())
    self.assertTrue((self.root / "reboot-required").exists())

  def test_pending_value_is_idempotent(self) -> None:
    self.variable.write_bytes(struct.pack("<II", 7, 1))
    result = self.run_helper("auto")
    self.assertEqual(0, result.returncode, result.stderr)
    self.assertIn("already pending", result.stdout)
    self.assertEqual(struct.pack("<II", 7, 1), self.variable.read_bytes())

  def test_auto_refuses_loop_after_consumed_override(self) -> None:
    state = self.root / "state"
    state.mkdir()
    (state / "armed-bios-version").write_text("test-bios\n")
    result = self.run_helper("auto")
    self.assertEqual(1, result.returncode)
    self.assertIn("refusing a reboot loop", result.stderr)
    self.assertEqual(struct.pack("<II", 7, 2), self.variable.read_bytes())
    self.assertFalse((self.root / "reboot-required").exists())

  def test_explicit_enable_can_retry_after_failed_attempt(self) -> None:
    state = self.root / "state"
    state.mkdir()
    (state / "armed-bios-version").write_text("test-bios\n")
    result = self.run_helper("enable")
    self.assertEqual(0, result.returncode, result.stderr)
    self.assertEqual(struct.pack("<II", 7, 1), self.variable.read_bytes())


if __name__ == "__main__":
  unittest.main()
