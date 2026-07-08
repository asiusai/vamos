#!/usr/bin/env python3
"""Focused tests for run_vfe_tuning_sweep.py host-side helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

import run_vfe_tuning_sweep as sweep


class EnvComboTest(unittest.TestCase):
  def test_parse_named_env_combo(self) -> None:
    combo = sweep.parse_env_combo(
      "gamma:ASIUS_PHYS_CAM2_GAMMA_K=18,ASIUS_PHYS_CAM3_GAMMA_K=15"
    )

    self.assertEqual("gamma", combo.name)
    self.assertEqual((
      "ASIUS_PHYS_CAM2_GAMMA_K=18",
      "ASIUS_PHYS_CAM3_GAMMA_K=15",
    ), combo.env)

  def test_parse_rejects_invalid_env_name(self) -> None:
    with self.assertRaises(ValueError):
      sweep.parse_env_combo("bad:1INVALID=value")


class CandidateTest(unittest.TestCase):
  def test_build_candidates_crosses_targets_and_env_combos(self) -> None:
    candidates = sweep.build_candidates(
      [0.0, 0.45],
      [
        sweep.EnvCombo("default", ()),
        sweep.EnvCombo("gamma", ("ASIUS_CAM_GAMMA_K=18",)),
      ],
    )

    self.assertEqual([
      "default-tg-default",
      "default-tg0p45",
      "gamma-tg-default",
      "gamma-tg0p45",
    ], [candidate.name for candidate in candidates])

  def test_build_capture_cmd_passes_candidate_env(self) -> None:
    args = SimpleNamespace(
      openpilot_dir="/data/openpilot_hw_vfe",
      settle=7.0,
      monitor_duration=15.0,
      profile="daylight-road",
      pull_timeout=60.0,
      require_ae_rgb_clip_guard=True,
      min_ae_samples=3,
      min_ae_rgb_clip=0.079,
      min_ae_ev_cap=0.05,
    )
    candidate = sweep.Candidate(
      "gamma-tg0p45",
      0.45,
      ("ASIUS_PHYS_CAM2_GAMMA_K=18", "ASIUS_PHYS_CAM3_GAMMA_K=15"),
    )

    cmd = sweep.build_capture_cmd(args, candidate, Path("/tmp/out"))

    self.assertIn("--validate-vfe", cmd)
    self.assertIn("--validate-quality-profile", cmd)
    self.assertIn("daylight-road", cmd)
    self.assertIn("--validate-ae-rgb-clip-guard", cmd)
    self.assertIn("0.45", cmd)
    self.assertIn("ASIUS_PHYS_CAM2_GAMMA_K=18", cmd)
    self.assertIn("ASIUS_PHYS_CAM3_GAMMA_K=15", cmd)


class RankingTest(unittest.TestCase):
  def test_candidate_sort_key_prefers_passing_cleaner_candidate(self) -> None:
    noisy = {
      "name": "noisy",
      "passed": True,
      "hardware_path_passed": True,
      "image_quality_passed": True,
      "failures": [],
      "cameras": {
        "cam2": {"y_median": 100, "rgb_median_spread": 20, "uv_hf_abs_mean": 5.0,
                 "tile_luma_clip_hi_area_frac_gt_10pct": 0.08,
                 "tile_luma_clip_hi_area_frac_gt_50pct": 0.02},
        "cam3": {"y_median": 100, "rgb_median_spread": 20, "uv_hf_abs_mean": 5.0,
                 "tile_luma_clip_hi_area_frac_gt_10pct": 0.08,
                 "tile_luma_clip_hi_area_frac_gt_50pct": 0.02},
      },
    }
    cleaner = {
      "name": "cleaner",
      "passed": True,
      "hardware_path_passed": True,
      "image_quality_passed": True,
      "failures": [],
      "cameras": {
        "cam2": {"y_median": 115, "rgb_median_spread": 5, "uv_hf_abs_mean": 2.0,
                 "tile_luma_clip_hi_area_frac_gt_10pct": 0.01,
                 "tile_luma_clip_hi_area_frac_gt_50pct": 0.0},
        "cam3": {"y_median": 116, "rgb_median_spread": 5, "uv_hf_abs_mean": 2.0,
                 "tile_luma_clip_hi_area_frac_gt_10pct": 0.01,
                 "tile_luma_clip_hi_area_frac_gt_50pct": 0.0},
      },
    }
    failing = {
      "name": "failing",
      "passed": False,
      "hardware_path_passed": True,
      "image_quality_passed": False,
      "failures": ["image failed"],
      "cameras": {},
    }

    ranked = sorted([noisy, failing, cleaner], key=sweep.candidate_sort_key)

    self.assertEqual(["cleaner", "noisy", "failing"], [candidate["name"] for candidate in ranked])


if __name__ == "__main__":
  unittest.main()
