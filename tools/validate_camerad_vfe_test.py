#!/usr/bin/env python3
"""Focused tests for validate_camerad_vfe.py host-side helpers."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
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


class VfeSetupSummaryTest(unittest.TestCase):
  def test_summarizes_vfe_setup_from_startup_log(self) -> None:
    log_text = "\n".join([
      "cam 1: VFE PIX source format 1344x760 code=0x2004",
      "cam 1: VIPC buffers created (VFE PIX V4L2 DMABUF NV12, 1344x760, scale=2, 2428928 bytes, stride=2048)",
      "cam 1: OS04 AWB enabled start=40 interval=20 deadband=1 response=2 step=8 y=40-235 chroma=24 min_samples=64 blue=0xfc red=0x100 range=0x40",
      "cam 1: wrote 15 poststart overrides VFE regs",
      "cam 1: wrote OS04 gamma DMI override g=18.00 b=18.00 r=18.00",
      "cam 0: VFE PIX source format 1344x760 code=0x2004",
      "cam 0: VIPC buffers created (VFE PIX V4L2 DMABUF NV12, 1344x760, scale=2, 2428928 bytes, stride=2048)",
      "cam 0: OS04 AWB enabled start=40 interval=20 deadband=1 response=2 step=8 y=40-235 chroma=24 min_samples=64 blue=0x100 red=0x100 range=0x40",
      "cam 0: wrote 15 poststart overrides VFE regs",
      "cam 0: wrote OS04 gamma DMI override g=15.00 b=15.00 r=15.00",
    ])
    report = validator.Report()
    summary: dict = {}

    with redirect_stdout(io.StringIO()):
      validator.summarize_vfe_setup_log(log_text, ["cam2", "cam3"], report, summary)

    cam2 = summary["vfe_setup"]["cameras"]["cam2"]
    cam3 = summary["vfe_setup"]["cameras"]["cam3"]
    self.assertEqual("VFE PIX V4L2 DMABUF NV12", cam2["vipc"]["mode"])
    self.assertEqual(1344, cam2["source_format"]["width"])
    self.assertEqual("0x2004", cam2["source_format"]["code_hex"])
    self.assertEqual(15, cam2["poststart_reg_count"])
    self.assertEqual(18.0, cam2["gamma"]["g"])
    self.assertEqual(0xfc, cam2["awb_config"]["blue"])
    self.assertEqual(0x40, cam2["awb_config"]["range"])
    self.assertEqual(15.0, cam3["gamma"]["g"])
    self.assertIs(summary["cameras"]["cam2"]["vfe_setup"], cam2)


class NoCpuImagePathSummaryTest(unittest.TestCase):
  def _args(self) -> SimpleNamespace:
    return SimpleNamespace(max_camerad_cpu_pct=10.0)

  def _summary(self) -> dict:
    return {
      "log": {
        "fallback_markers": {
          "falling back to RDI": False,
          "NV12 sw debayer": False,
          "VFE PIX unavailable": False,
          "falling back to V4L2 MMAP CPU-copy path": False,
        },
        "dmabuf_fallback": False,
      },
      "category_passed": {
        "cpu": True,
      },
      "artifacts": {
        "require_latest_raw_match": True,
      },
      "cpu": {
        "available": True,
        "single_core_cpu_pct": 1.2,
      },
      "cameras": {
        "cam2": {
          "vfe_pix_v4l2": True,
          "dmabuf_nv12": True,
          "vfe_setup": {
            "vipc": {
              "mode": "VFE PIX V4L2 DMABUF NV12",
            },
          },
          "artifacts": {
            "latest_raw_match": True,
          },
        },
        "cam3": {
          "vfe_pix_v4l2": True,
          "dmabuf_nv12": True,
          "vfe_setup": {
            "vipc": {
              "mode": "VFE PIX V4L2 DMABUF NV12",
            },
          },
          "artifacts": {
            "latest_raw_match": True,
          },
        },
      },
    }

  def test_verifies_complete_vfe_dmabuf_path(self) -> None:
    path = validator.summarize_no_cpu_image_path(self._summary(), ["cam2", "cam3"], self._args())

    self.assertTrue(path["verified"])
    self.assertIn("VFE PIX", path["name"])
    self.assertTrue(path["requirements"]["latest_images_are_raw_vfe_jpegs"])
    self.assertEqual("VFE PIX V4L2 DMABUF NV12", path["cameras"]["cam2"]["vipc_mode"])
    self.assertEqual(1.2, path["cpu"]["single_core_cpu_pct"])

  def test_requires_raw_vfe_artifact_match(self) -> None:
    summary = self._summary()
    summary["cameras"]["cam3"]["artifacts"]["latest_raw_match"] = False

    path = validator.summarize_no_cpu_image_path(summary, ["cam2", "cam3"], self._args())

    self.assertFalse(path["verified"])
    self.assertFalse(path["requirements"]["latest_images_are_raw_vfe_jpegs"])


class DmesgForbiddenPatternTest(unittest.TestCase):
  def test_flags_vfe_pix_stall_and_recovery_warnings(self) -> None:
    dmesg_text = "\n".join([
      "[  42.000000] qcom-camss ac5a000.camss: vfe0 pix recovering missing PIX wm done mask=0x1 missing=1 seq=20 sof=21 wm3=21 wm4=20",
      "[  43.000000] qcom-camss ac5a000.camss: vfe1 pix stall? sof=40 reg=40 comp=39 dual=0 wm3=39 wm4=39 gap=20",
    ])

    matches = validator.forbidden_dmesg_matches(dmesg_text)

    self.assertEqual(2, len(matches))
    self.assertTrue(all(match["kind"] == "VFE PIX stall/recovery warning" for match in matches))


class LatestRawMatchTest(unittest.TestCase):
  def _args(self) -> SimpleNamespace:
    return SimpleNamespace(require_latest_raw_match=True)

  def _write_pair(self, run_dir: Path, cam: str, latest: bytes, raw: bytes) -> None:
    (run_dir / validator.IMAGE_FILES[cam]).write_bytes(latest)
    (run_dir / validator.RAW_IMAGE_FILES[cam]).write_bytes(raw)

  def test_passes_when_latest_matches_raw_vfe_jpeg(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      run_dir = Path(tmp)
      self._write_pair(run_dir, "cam2", b"same-jpeg-bytes", b"same-jpeg-bytes")
      report = validator.Report()
      summary: dict = {}

      with redirect_stdout(io.StringIO()):
        validator.validate_latest_raw_match(run_dir, ["cam2"], self._args(), report, summary)

      self.assertEqual([], report.failures)
      self.assertTrue(summary["artifacts"]["cameras"]["cam2"]["latest_raw_match"])
      self.assertEqual(
        summary["artifacts"]["cameras"]["cam2"],
        summary["cameras"]["cam2"]["artifacts"],
      )

  def test_fails_when_latest_differs_from_raw_vfe_jpeg(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      run_dir = Path(tmp)
      self._write_pair(run_dir, "cam3", b"preview-enhanced", b"raw-vfe")
      report = validator.Report()
      summary: dict = {}

      with redirect_stdout(io.StringIO()):
        validator.validate_latest_raw_match(run_dir, ["cam3"], self._args(), report, summary)

      self.assertFalse(summary["artifacts"]["cameras"]["cam3"]["latest_raw_match"])
      self.assertTrue(any("latest JPEG differs from raw VFE JPEG" in failure for failure in report.failures))


if __name__ == "__main__":
  unittest.main()
