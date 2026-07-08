#!/usr/bin/env python3
"""Focused tests for validate_camerad_vfe.py host-side helpers."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
from types import SimpleNamespace
import unittest

import validate_camerad_vfe as validator


class QualityProfileTest(unittest.TestCase):
  def _args(self) -> SimpleNamespace:
    return SimpleNamespace(
      min_y_median=20.0,
      max_y_median=235.0,
      min_y_range=30.0,
      max_y_clip_lo=0.30,
      max_y_clip_hi=0.30,
      max_luma_clip_hi=0.30,
      max_rgb_spread=50.0,
      max_uv_median_offset=999.0,
      max_uv_center_median_offset=999.0,
      min_mean_chroma=1.0,
      min_uv_abs=1.0,
      max_tile_uv_median_offset=999.0,
      max_tile_uv_median_range=999.0,
      max_tile_rgb_spread=999.0,
      max_tile_luma_clip_hi_area_frac_gt_10pct=999.0,
      max_tile_luma_clip_hi_area_frac_gt_50pct=999.0,
      max_uv_hf_abs_mean=999.0,
    )

  def test_daylight_road_adds_area_clipping_limits(self) -> None:
    args = self._args()
    args.quality_profile = "daylight-road"

    validator.apply_quality_profile(args)

    self.assertEqual(0.12, args.max_tile_luma_clip_hi_area_frac_gt_10pct)
    self.assertEqual(0.04, args.max_tile_luma_clip_hi_area_frac_gt_50pct)

  def test_road_spatial_does_not_gate_clipped_tile_area(self) -> None:
    args = self._args()
    args.quality_profile = "road-spatial"

    validator.apply_quality_profile(args)

    self.assertEqual(999.0, args.max_tile_luma_clip_hi_area_frac_gt_10pct)
    self.assertEqual(999.0, args.max_tile_luma_clip_hi_area_frac_gt_50pct)


class AeRgbClipGuardTest(unittest.TestCase):
  def _args(self, require: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
      require_ae_rgb_clip_guard=require,
      min_ae_samples=2,
      min_ae_rgb_clip=0.079,
      min_ae_ev_cap=0.05,
    )

  def _validate(self, log_text: str, require: bool = True) -> tuple[validator.Report, dict]:
    report = validator.Report()
    summary: dict = {}
    with redirect_stdout(io.StringIO()):
      validator.validate_ae_rgb_clip_guard(log_text, ["cam2", "cam3"], self._args(require), report, summary)
    return report, summary

  def test_requires_samples_for_each_selected_camera(self) -> None:
    log_text = "\n".join([
      "cam 0: OS04 AE grey=0.4100 target=0.4200 rgb_clip=0.0900 cur_ev=36.00 desired_ev=35.00 unclipped_ev=36.00",
      "cam 0: OS04 AE grey=0.4100 target=0.4200 rgb_clip=0.0910 cur_ev=36.00 desired_ev=35.00 unclipped_ev=36.00",
    ])

    report, summary = self._validate(log_text)

    self.assertFalse(summary["ae"]["cameras"]["cam2"]["samples"])
    self.assertTrue(any("cam2: only 0 OS04 AE samples" in failure for failure in report.failures))

  def test_passes_when_any_selected_camera_caps_highlight_ev(self) -> None:
    log_text = "\n".join([
      "cam 1: OS04 AE grey=0.4023 target=0.3800 rgb_clip=0.0600 cur_ev=18.00 desired_ev=17.50 unclipped_ev=17.00 exp 14->13 gain_idx 0->0 gain 1.000",
      "cam 1: OS04 AE grey=0.4023 target=0.3800 rgb_clip=0.0610 cur_ev=18.00 desired_ev=17.50 unclipped_ev=17.00 exp 13->13 gain_idx 0->0 gain 1.000",
      "cam 0: OS04 AE grey=0.4102 target=0.4200 rgb_clip=0.0800 cur_ev=36.00 desired_ev=35.90 unclipped_ev=36.20",
      "cam 0: OS04 AE grey=0.4102 target=0.4200 rgb_clip=0.0820 cur_ev=36.00 desired_ev=35.70 unclipped_ev=36.40",
    ])

    report, summary = self._validate(log_text)

    self.assertEqual([], report.failures)
    self.assertEqual(13, summary["ae"]["cameras"]["cam2"]["last"]["exp"])
    self.assertEqual(0, summary["ae"]["cameras"]["cam2"]["last"]["gain"])
    self.assertEqual(1.0, summary["ae"]["cameras"]["cam2"]["window"]["gain_factor_median"])
    self.assertIs(summary["cameras"]["cam2"]["ae"], summary["ae"]["cameras"]["cam2"])
    self.assertEqual(2, summary["ae"]["cameras"]["cam3"]["guard_active_samples"])
    self.assertEqual(2, summary["ae"]["guard_active_samples"])

  def test_fails_when_rgb_clip_never_caps_ev(self) -> None:
    log_text = "\n".join([
      "cam 1: OS04 AE grey=0.4023 target=0.3800 rgb_clip=0.0600 cur_ev=18.00 desired_ev=17.50 unclipped_ev=17.00",
      "cam 1: OS04 AE grey=0.4023 target=0.3800 rgb_clip=0.0610 cur_ev=18.00 desired_ev=17.50 unclipped_ev=17.00",
      "cam 0: OS04 AE grey=0.4102 target=0.4200 rgb_clip=0.0780 cur_ev=36.00 desired_ev=36.40 unclipped_ev=36.50",
      "cam 0: OS04 AE grey=0.4102 target=0.4200 rgb_clip=0.0785 cur_ev=36.00 desired_ev=36.40 unclipped_ev=36.50",
    ])

    report, summary = self._validate(log_text)

    self.assertEqual(0, summary["ae"]["guard_active_samples"])
    self.assertTrue(any("never capped EV" in failure for failure in report.failures))


class AwbSummaryTest(unittest.TestCase):
  def test_summarizes_awb_samples_per_camera(self) -> None:
    log_text = "\n".join([
      "cam 1: OS04 AWB stable U=127 V=126 samples=3917 neutral=3917 blue=0x12c red=0x130",
      "cam 1: OS04 AWB stable U=128 V=127 samples=3920 neutral=3920 blue=0x12e red=0x132",
      "cam 0: OS04 AWB U=129 V=128 samples=3900 neutral=3900 blue=0x116 red=0x11a",
    ])
    report = validator.Report()
    summary: dict = {}

    with redirect_stdout(io.StringIO()):
      validator.summarize_awb_log(log_text, ["cam2", "cam3"], report, summary)

    self.assertEqual(2, summary["awb"]["cameras"]["cam2"]["samples"])
    self.assertEqual(2, summary["awb"]["cameras"]["cam2"]["stable_samples"])
    self.assertEqual(302, summary["awb"]["cameras"]["cam2"]["last"]["blue"])
    self.assertEqual(301.0, summary["awb"]["cameras"]["cam2"]["window"]["blue_median"])
    self.assertEqual(1, summary["awb"]["cameras"]["cam3"]["samples"])
    self.assertEqual(0, summary["awb"]["cameras"]["cam3"]["stable_samples"])
    self.assertIs(summary["cameras"]["cam2"]["awb"], summary["awb"]["cameras"]["cam2"])


if __name__ == "__main__":
  unittest.main()
