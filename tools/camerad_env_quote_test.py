#!/usr/bin/env python3
"""Tests for multiline remote env quoting in camera validation helpers."""

from __future__ import annotations

import unittest

import camerad_capture_latest as capture


class BashEnvWordTest(unittest.TestCase):
  def test_multiline_env_uses_one_line_bash_ansi_quote(self) -> None:
    value = "ASIUS_PHYS_CAM2_VFE_REG_OVERRIDES=0xf40=0x01aa1ee7\n0xf44=0x00001f70"

    self.assertEqual(
      "$'ASIUS_PHYS_CAM2_VFE_REG_OVERRIDES=0xf40=0x01aa1ee7\\n0xf44=0x00001f70'",
      capture.bash_env_word(value),
    )

  def test_plain_env_still_uses_shlex_quote(self) -> None:
    self.assertEqual("ASIUS_CAM_GAMMA_K=12", capture.bash_env_word("ASIUS_CAM_GAMMA_K=12"))
    self.assertEqual("'NAME=value with space'", capture.bash_env_word("NAME=value with space"))


class RemoteScriptTest(unittest.TestCase):
  def test_capture_script_multiline_env_does_not_break_heredoc_dedent(self) -> None:
    script = capture.remote_script(
      "/data/openpilot",
      "both",
      0.1,
      600,
      0.0,
      1.0,
      115.0,
      False,
      False,
      False,
      0.1,
      True,
      False,
      False,
      False,
      ["ASIUS_PHYS_CAM2_VFE_REG_OVERRIDES=0xf40=0x01aa1ee7\n0xf44=0x00001f70"],
    )

    export_line = next(line for line in script.splitlines() if "ASIUS_PHYS_CAM2_VFE_REG_OVERRIDES" in line)
    self.assertIn("$'ASIUS_PHYS_CAM2_VFE_REG_OVERRIDES=0xf40=0x01aa1ee7\\n0xf44=0x00001f70'", export_line)
    self.assertIn("\n/usr/local/venv/bin/python - <<'PY'\n", script)
    self.assertIn("\nPY\n", script)

if __name__ == "__main__":
  unittest.main()
