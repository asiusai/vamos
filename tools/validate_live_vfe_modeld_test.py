#!/usr/bin/env python3
"""Focused tests for validate_live_vfe_modeld.py host-side helpers."""

from __future__ import annotations

import unittest

import validate_live_vfe_modeld as validator


class CameradHardwarePathSummaryTest(unittest.TestCase):
  def test_passes_when_both_cameras_use_vfe_dmabuf_nv12(self) -> None:
    log_text = "\n".join([
      "cam 1: VIPC buffers created (VFE PIX V4L2 DMABUF NV12 1344x760 stride=2048)",
      "cam 0: VIPC buffers created (VFE PIX V4L2 DMABUF NV12 1344x760 stride=2048)",
    ])

    summary, failures = validator.camerad_hardware_path_summary(log_text)

    self.assertEqual([], failures)
    self.assertTrue(summary["cameras"]["cam2"]["vfe_pix_v4l2"])
    self.assertTrue(summary["cameras"]["cam2"]["dmabuf_nv12"])
    self.assertTrue(summary["cameras"]["cam3"]["vfe_pix_v4l2"])
    self.assertTrue(summary["cameras"]["cam3"]["dmabuf_nv12"])

  def test_fails_when_selected_camera_does_not_use_dmabuf_nv12(self) -> None:
    log_text = "\n".join([
      "cam 1: VIPC buffers created (VFE PIX V4L2 MMAP NV12 1344x760 stride=2048)",
      "cam 0: VIPC buffers created (VFE PIX V4L2 DMABUF NV12 1344x760 stride=2048)",
    ])

    summary, failures = validator.camerad_hardware_path_summary(log_text)

    self.assertTrue(summary["cameras"]["cam2"]["vfe_pix_v4l2"])
    self.assertFalse(summary["cameras"]["cam2"]["dmabuf_nv12"])
    self.assertTrue(any("cam2 missing VFE PIX V4L2 DMABUF NV12" in failure for failure in failures))

  def test_fails_on_forbidden_fallback_marker(self) -> None:
    summary, failures = validator.camerad_hardware_path_summary("falling back to RDI")

    self.assertTrue(summary["fatal_markers"]["falling back to RDI"])
    self.assertTrue(any("forbidden fallback marker" in failure for failure in failures))


class DmesgForbiddenPatternTest(unittest.TestCase):
  def test_flags_vfe_pix_stall_and_recovery_warnings(self) -> None:
    dmesg_text = "\n".join([
      "[  42.000000] qcom-camss ac5a000.camss: vfe0 pix recovering missing PIX wm done mask=0x1 missing=1 seq=20 sof=21 wm3=21 wm4=20",
      "[  43.000000] qcom-camss ac5a000.camss: vfe1 pix stall? sof=40 reg=40 comp=39 dual=0 wm3=39 wm4=39 gap=20",
    ])

    matches = validator.forbidden_dmesg_matches(dmesg_text)

    self.assertEqual(2, len(matches))
    self.assertTrue(all(match["kind"] == "VFE PIX stall/recovery warning" for match in matches))


if __name__ == "__main__":
  unittest.main()
