#!/usr/bin/env python3
"""Tests for Firehose storage selection."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


HELPER = Path(__file__).with_name("edl_usb.sh")


class EdlStorageDetectionTest(unittest.TestCase):
  def setUp(self) -> None:
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.bin_dir = Path(self.temporary.name)
    self.calls = self.bin_dir / "calls"
    self._executable("sudo", '#!/bin/sh\nexec "$@"\n')
    self._executable(
      "edl-ng",
      """#!/bin/sh
memory=
slot=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --memory=*) memory="${1#--memory=}" ;;
    --slot=*) slot="${1#--slot=}" ;;
    read-sector)
      output="$4"
      break
      ;;
  esac
  shift
done
printf '%s:%s\\n' "$memory" "$slot" >> "$EDL_TEST_CALLS"
if [ "$memory:$slot" = Sdcc:0 ]; then
  dd if=/dev/zero of="$output" bs=512 count=1 status=none
  exit 0
fi
exit 1
""",
    )

  def _executable(self, name: str, source: str) -> None:
    path = self.bin_dir / name
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)

  def run_helper(self, body: str, **extra_environment: str) -> subprocess.CompletedProcess[str]:
    environment = {
      **os.environ,
      "EDL_TEST_CALLS": str(self.calls),
      "PATH": f"{self.bin_dir}:{os.environ['PATH']}",
      **extra_environment,
    }
    return subprocess.run(
      ["bash", "-c", f'. "{HELPER}"; {body}'],
      check=False,
      text=True,
      stdout=subprocess.PIPE,
      stderr=subprocess.STDOUT,
      env=environment,
    )

  def test_selects_emmc_after_ufs_and_nvme(self) -> None:
    result = self.run_helper(
      'detect_edl_storage loader; printf "RESULT=%s|%s\\nTRANSPORT=%s\\n" "${EDL_STORAGE_ARGS[*]}" "$EDL_STORAGE_LABEL" "${EDL_TRANSPORT_ARGS[*]}"'
    )

    self.assertEqual(result.returncode, 0, result.stdout)
    self.assertEqual(self.calls.read_text(encoding="utf-8").splitlines(), ["Ufs:0", "Nvme:0", "Sdcc:0"])
    self.assertIn("RESULT=--memory=Sdcc --slot=0|eMMC", result.stdout)
    self.assertIn("TRANSPORT=--maxpayload=65536", result.stdout)

  def test_explicit_sd_override_skips_probes(self) -> None:
    result = self.run_helper(
      'detect_edl_storage loader; printf "RESULT=%s|%s\\n" "${EDL_STORAGE_ARGS[*]}" "$EDL_STORAGE_LABEL"',
      VAMOS_EDL_MEMORY="Sdcc",
      VAMOS_EDL_SLOT="1",
    )

    self.assertEqual(result.returncode, 0, result.stdout)
    self.assertFalse(self.calls.exists())
    self.assertIn("RESULT=--memory=Sdcc --slot=1|eMMC/SDCC slot 1 (explicit)", result.stdout)


if __name__ == "__main__":
  unittest.main()
