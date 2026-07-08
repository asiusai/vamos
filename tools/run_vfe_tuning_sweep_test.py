#!/usr/bin/env python3
"""Focused tests for run_vfe_tuning_sweep.py host-side helpers."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
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


class PublicCcmTest(unittest.TestCase):
  def test_vfe_ccm_override_env_quantizes_public_os04_matrix(self) -> None:
    env = sweep.vfe_ccm_override_env(sweep.PUBLIC_OS04_CCM[6500])

    self.assertTrue(env.startswith("ASIUS_CAM_VFE_REG_OVERRIDES="))
    self.assertIn("0x760=0x0000017f", env)
    self.assertIn("0x764=0x00000fd0", env)
    self.assertIn("0x780=0x000001b0", env)
    self.assertIn("0x790=0x00000000", env)
    self.assertEqual(13, len(env.split("=", 1)[1].splitlines()))
    self.assertIn("\n", env)
    self.assertNotIn(",", env)

  def test_public_os04_ccm_combo_can_add_gamma_without_splitting_registers(self) -> None:
    combo = sweep.parse_env_combo(sweep.public_os04_ccm_combo(6500, gamma=20))

    self.assertEqual("pubccm6500-gamma20", combo.name)
    self.assertEqual(2, len(combo.env))
    self.assertTrue(combo.env[0].startswith("ASIUS_CAM_VFE_REG_OVERRIDES="))
    self.assertIn("0x760=0x0000017f", combo.env[0])
    self.assertIn("\n0x764=0x00000fd0", combo.env[0])
    self.assertEqual("ASIUS_CAM_GAMMA_K=20", combo.env[1])


class CstChromaTest(unittest.TestCase):
  def test_cst_chroma_combo_uses_one_multiline_vfe_override(self) -> None:
    combo = sweep.parse_env_combo(sweep.cst_chroma_combo("cst1p2", gamma=20))

    self.assertEqual("cst1p2-gamma20", combo.name)
    self.assertEqual(2, len(combo.env))
    self.assertTrue(combo.env[0].startswith("ASIUS_CAM_VFE_REG_OVERRIDES="))
    self.assertIn("0xf40=0x021a1e9d", combo.env[0])
    self.assertIn("\n0xf54=0x0000021a", combo.env[0])
    self.assertEqual(12, len(combo.env[0].split("=", 1)[1].splitlines()))
    self.assertNotIn(",", combo.env[0])
    self.assertEqual("ASIUS_CAM_GAMMA_K=20", combo.env[1])


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

  def test_select_sweep_inputs_expands_named_preset(self) -> None:
    args = SimpleNamespace(
      preset="os04-daylight-v1",
      target_greys=None,
      env_combo=None,
    )

    target_greys, env_combo_specs = sweep.select_sweep_inputs(args)

    self.assertEqual([0.0, 0.45], target_greys)
    self.assertIn("gamma20:ASIUS_CAM_GAMMA_K=20", env_combo_specs)
    self.assertIn(
      "split20-18:ASIUS_PHYS_CAM2_GAMMA_K=20,ASIUS_PHYS_CAM3_GAMMA_K=18",
      env_combo_specs,
    )

  def test_select_sweep_inputs_expands_public_ccm_preset(self) -> None:
    args = SimpleNamespace(
      preset="os04-public-ccm-v1",
      target_greys=None,
      env_combo=None,
    )

    target_greys, env_combo_specs = sweep.select_sweep_inputs(args)

    self.assertEqual([0.0, 0.45], target_greys)
    self.assertEqual(6, len(env_combo_specs))
    self.assertIn("gamma20:ASIUS_CAM_GAMMA_K=20", env_combo_specs)
    self.assertTrue(any("pubccm5000" in spec for spec in env_combo_specs))
    self.assertTrue(any("pubccm6500" in spec for spec in env_combo_specs))
    self.assertTrue(any("ASIUS_CAM_VFE_REG_OVERRIDES=" in spec for spec in env_combo_specs))

  def test_select_sweep_inputs_expands_cst_chroma_preset(self) -> None:
    args = SimpleNamespace(
      preset="os04-cst-chroma-v1",
      target_greys=None,
      env_combo=None,
    )

    target_greys, env_combo_specs = sweep.select_sweep_inputs(args)

    self.assertEqual([0.0, 0.45], target_greys)
    self.assertEqual(6, len(env_combo_specs))
    self.assertIn("gamma20:ASIUS_CAM_GAMMA_K=20", env_combo_specs)
    self.assertTrue(any("cst1p0" in spec for spec in env_combo_specs))
    self.assertTrue(any("cst1p2" in spec for spec in env_combo_specs))
    self.assertTrue(any("0xf40=0x01c01ed8" in spec for spec in env_combo_specs))

  def test_select_sweep_inputs_allows_explicit_preset_overrides(self) -> None:
    args = SimpleNamespace(
      preset="os04-daylight-v1",
      target_greys=[0.5],
      env_combo=["custom:ASIUS_CAM_GAMMA_K=16"],
    )

    target_greys, env_combo_specs = sweep.select_sweep_inputs(args)

    self.assertEqual([0.5], target_greys)
    self.assertEqual(["custom:ASIUS_CAM_GAMMA_K=16"], env_combo_specs)

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

  def test_build_acceptance_cmd_preserves_candidate_knobs(self) -> None:
    args = SimpleNamespace(
      openpilot_dir="/data/openpilot_hw_vfe",
      pull_timeout=60.0,
      require_ae_rgb_clip_guard=True,
      min_ae_samples=3,
      min_ae_rgb_clip=0.079,
      min_ae_ev_cap=0.05,
      acceptance_snapshot_settle=7.0,
      acceptance_snapshot_duration=120.0,
      acceptance_modeld_settle=7.0,
      acceptance_modeld_duration=120.0,
    )
    candidate = sweep.Candidate(
      "split20-18-tg0p45",
      0.45,
      ("ASIUS_PHYS_CAM2_GAMMA_K=20", "ASIUS_PHYS_CAM3_GAMMA_K=18"),
    )

    cmd = sweep.build_acceptance_cmd(args, candidate, Path("/tmp/acceptance"))

    self.assertIn("run_vfe_acceptance.py", cmd[1])
    self.assertIn("--snapshot-profile", cmd)
    self.assertIn("daylight-road", cmd)
    self.assertIn("--snapshot-monitor-duration", cmd)
    self.assertIn("120.0", cmd)
    self.assertIn("--modeld-duration", cmd)
    self.assertIn("--target-grey", cmd)
    self.assertIn("0.45", cmd)
    self.assertIn("ASIUS_PHYS_CAM2_GAMMA_K=20", cmd)
    self.assertIn("ASIUS_PHYS_CAM3_GAMMA_K=18", cmd)
    self.assertIn("--min-ae-samples", cmd)

  def test_build_acceptance_cmd_can_disable_ae_clip_guard(self) -> None:
    args = SimpleNamespace(
      openpilot_dir="/data/openpilot_hw_vfe",
      pull_timeout=60.0,
      require_ae_rgb_clip_guard=False,
      min_ae_samples=3,
      min_ae_rgb_clip=0.079,
      min_ae_ev_cap=0.05,
      acceptance_snapshot_settle=7.0,
      acceptance_snapshot_duration=120.0,
      acceptance_modeld_settle=7.0,
      acceptance_modeld_duration=120.0,
    )
    candidate = sweep.Candidate("default", 0.0, ())

    cmd = sweep.build_acceptance_cmd(args, candidate, Path("/tmp/acceptance"))

    self.assertIn("--no-require-ae-rgb-clip-guard", cmd)
    self.assertNotIn("--min-ae-samples", cmd)

  def test_finalize_command_template_points_at_acceptance_summary(self) -> None:
    cmd = sweep.build_finalize_cmd_template(Path("/tmp/acceptance"))

    self.assertIn("--finalize-existing-summary", cmd)
    self.assertIn("/tmp/acceptance/vfe-acceptance-summary.json", cmd)
    self.assertIn("--visual-check-montage-sha256", cmd)
    self.assertIn("<reviewed-montage-sha256>", cmd)
    self.assertIn("--require-final-acceptance", cmd)

  def test_acceptance_script_execs_exact_command(self) -> None:
    text = sweep.build_acceptance_script_text([
      "/usr/bin/python3",
      "/tmp/run_vfe_acceptance.py",
      "--env",
      "ASIUS_PHYS_CAM2_GAMMA_K=20",
    ])

    self.assertTrue(text.startswith("#!/usr/bin/env bash\n"))
    self.assertIn("set -euo pipefail", text)
    self.assertIn("exec /usr/bin/python3 /tmp/run_vfe_acceptance.py --env ASIUS_PHYS_CAM2_GAMMA_K=20", text)

  def test_finalize_script_accepts_sha_and_note_arguments(self) -> None:
    text = sweep.build_finalize_script_text(Path("/tmp/acceptance"))

    self.assertIn("usage: $0 <reviewed-montage-sha256> <human-review-note>", text)
    self.assertIn("/tmp/acceptance/vfe-acceptance-summary.json", text)
    self.assertIn('"$human_review_note"', text)
    self.assertIn('"$reviewed_montage_sha256"', text)
    self.assertIn("--require-final-acceptance", text)

  def test_write_best_candidate_scripts_creates_executable_files(self) -> None:
    with TemporaryDirectory() as tmp:
      out_dir = Path(tmp)
      scripts = sweep.write_best_candidate_scripts(out_dir, {
        "acceptance_out_dir": str(out_dir / "acceptance-best"),
        "acceptance_command": [
          "/usr/bin/python3",
          "/tmp/run_vfe_acceptance.py",
          "--target-grey",
          "0.45",
        ],
      })

      acceptance_script = Path(scripts["acceptance_script"])
      finalize_script = Path(scripts["finalize_script"])
      self.assertTrue(acceptance_script.exists())
      self.assertTrue(finalize_script.exists())
      self.assertTrue(os.access(acceptance_script, os.X_OK))
      self.assertTrue(os.access(finalize_script, os.X_OK))
      self.assertIn("--target-grey 0.45", acceptance_script.read_text())
      self.assertIn("acceptance-best/vfe-acceptance-summary.json", finalize_script.read_text())


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
                 "mean_chroma": 4.0, "max_uv_center_median_offset": 8.0,
                 "tile_luma_clip_hi_area_frac_gt_10pct": 0.08,
                 "tile_luma_clip_hi_area_frac_gt_50pct": 0.02},
        "cam3": {"y_median": 100, "rgb_median_spread": 20, "uv_hf_abs_mean": 5.0,
                 "mean_chroma": 4.0, "max_uv_center_median_offset": 8.0,
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
                 "mean_chroma": 10.0, "max_uv_center_median_offset": 1.0,
                 "tile_luma_clip_hi_area_frac_gt_10pct": 0.01,
                 "tile_luma_clip_hi_area_frac_gt_50pct": 0.0},
        "cam3": {"y_median": 116, "rgb_median_spread": 5, "uv_hf_abs_mean": 2.0,
                 "mean_chroma": 10.0, "max_uv_center_median_offset": 1.0,
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

  def test_candidate_sort_key_penalizes_grey_or_color_cast_candidates(self) -> None:
    grey = {
      "name": "grey",
      "passed": True,
      "hardware_path_passed": True,
      "image_quality_passed": True,
      "failures": [],
      "cameras": {
        "cam2": {"y_median": 115, "rgb_median_spread": 5, "uv_hf_abs_mean": 2.0,
                 "mean_chroma": 2.0, "max_uv_center_median_offset": 1.0,
                 "tile_luma_clip_hi_area_frac_gt_10pct": 0.0,
                 "tile_luma_clip_hi_area_frac_gt_50pct": 0.0},
        "cam3": {"y_median": 115, "rgb_median_spread": 5, "uv_hf_abs_mean": 2.0,
                 "mean_chroma": 2.0, "max_uv_center_median_offset": 1.0,
                 "tile_luma_clip_hi_area_frac_gt_10pct": 0.0,
                 "tile_luma_clip_hi_area_frac_gt_50pct": 0.0},
      },
    }
    cast = {
      "name": "cast",
      "passed": True,
      "hardware_path_passed": True,
      "image_quality_passed": True,
      "failures": [],
      "cameras": {
        "cam2": {"y_median": 115, "rgb_median_spread": 5, "uv_hf_abs_mean": 2.0,
                 "mean_chroma": 10.0, "max_uv_center_median_offset": 12.0,
                 "tile_luma_clip_hi_area_frac_gt_10pct": 0.0,
                 "tile_luma_clip_hi_area_frac_gt_50pct": 0.0},
        "cam3": {"y_median": 115, "rgb_median_spread": 5, "uv_hf_abs_mean": 2.0,
                 "mean_chroma": 10.0, "max_uv_center_median_offset": 12.0,
                 "tile_luma_clip_hi_area_frac_gt_10pct": 0.0,
                 "tile_luma_clip_hi_area_frac_gt_50pct": 0.0},
      },
    }
    balanced = {
      "name": "balanced",
      "passed": True,
      "hardware_path_passed": True,
      "image_quality_passed": True,
      "failures": [],
      "cameras": {
        "cam2": {"y_median": 115, "rgb_median_spread": 8, "uv_hf_abs_mean": 2.5,
                 "mean_chroma": 10.0, "max_uv_center_median_offset": 1.0,
                 "tile_luma_clip_hi_area_frac_gt_10pct": 0.0,
                 "tile_luma_clip_hi_area_frac_gt_50pct": 0.0},
        "cam3": {"y_median": 115, "rgb_median_spread": 8, "uv_hf_abs_mean": 2.5,
                 "mean_chroma": 10.0, "max_uv_center_median_offset": 1.0,
                 "tile_luma_clip_hi_area_frac_gt_10pct": 0.0,
                 "tile_luma_clip_hi_area_frac_gt_50pct": 0.0},
      },
    }

    ranked = sorted([grey, cast, balanced], key=sweep.candidate_sort_key)

    self.assertEqual("balanced", ranked[0]["name"])
    self.assertEqual("grey", ranked[-1]["name"])

  def test_candidate_quality_metrics_are_saved_for_explanation(self) -> None:
    result = {
      "cameras": {
        "cam2": {"y_median": 115, "rgb_median_spread": 5, "uv_hf_abs_mean": 2.0,
                 "mean_chroma": 10.0, "max_uv_center_median_offset": 1.0,
                 "tile_luma_clip_hi_area_frac_gt_10pct": 0.0,
                 "tile_luma_clip_hi_area_frac_gt_50pct": 0.0},
      },
    }

    metrics = sweep.candidate_quality_metrics(result)

    self.assertIn("color_defect", metrics)
    self.assertIn("chroma_weakness", metrics)
    self.assertIn("center_color_cast", metrics)


class LabelTest(unittest.TestCase):
  def test_candidate_label_rounds_float_metrics(self) -> None:
    label = sweep.candidate_label({
      "name": "default",
      "passed": True,
      "hardware_path_passed": True,
      "image_quality_passed": True,
      "target_grey": 0.45,
      "failures": [],
      "cameras": {
        "cam2": {
          "y_median": 101.0,
          "rgb_median_spread": 6.0,
          "mean_chroma": 9.0,
          "max_uv_center_median_offset": 2.0,
          "uv_hf_abs_mean": 3.382821843,
          "tile_luma_clip_hi_area_frac_gt_10pct": 0.04,
          "tile_luma_clip_hi_area_frac_gt_50pct": 0.0,
          "latest_raw_match": True,
        },
      },
    })

    self.assertIn(
      "cam2: y=101.00 rgb=6.00 chroma=9.00 centerUV=2.00 uvhf=3.38 clip=0.04/0.00 raw=True",
      label,
    )


class ContactSheetTest(unittest.TestCase):
  def test_build_contact_sheet_from_candidate_images(self) -> None:
    try:
      from PIL import Image
    except ImportError:
      self.skipTest("Pillow not available")

    with TemporaryDirectory() as tmp:
      root = Path(tmp)
      candidate_dir = root / "cleaner"
      candidate_dir.mkdir()
      Image.new("RGB", (64, 36), (200, 40, 40)).save(candidate_dir / "latest-camerad-road.jpg")
      Image.new("RGB", (64, 36), (40, 40, 200)).save(candidate_dir / "latest-camerad-wide.jpg")
      result = {
        "name": "cleaner",
        "out_dir": str(candidate_dir),
        "target_grey": 0.45,
        "passed": True,
        "hardware_path_passed": True,
        "image_quality_passed": True,
        "failures": [],
        "cameras": {
          "cam2": {"y_median": 115, "rgb_median_spread": 5, "uv_hf_abs_mean": 2.0,
                   "mean_chroma": 10.0, "max_uv_center_median_offset": 1.0,
                   "tile_luma_clip_hi_area_frac_gt_10pct": 0.01,
                   "tile_luma_clip_hi_area_frac_gt_50pct": 0.0},
          "cam3": {"y_median": 116, "rgb_median_spread": 5, "uv_hf_abs_mean": 2.0,
                   "mean_chroma": 10.0, "max_uv_center_median_offset": 1.0,
                   "tile_luma_clip_hi_area_frac_gt_10pct": 0.01,
                   "tile_luma_clip_hi_area_frac_gt_50pct": 0.0},
        },
      }

      sheet = sweep.build_contact_sheet([result], root, image_width=80)

      self.assertIsNotNone(sheet)
      self.assertTrue(sheet.exists())
      self.assertGreater(sheet.stat().st_size, 0)


if __name__ == "__main__":
  unittest.main()
