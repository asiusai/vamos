#!/usr/bin/env python3
"""Focused tests for run_vfe_acceptance.py host-side helpers."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import run_vfe_acceptance as acceptance


class CameraExtractTest(unittest.TestCase):
  def test_includes_raw_vfe_artifact_match(self) -> None:
    summary = {
      "cameras": {
        "cam2": {
          "vfe_pix_v4l2": True,
          "dmabuf_nv12": True,
          "artifacts": {
            "latest_raw_match": True,
            "latest_bytes": 123,
            "raw_bytes": 123,
            "latest_sha256": "abc",
            "raw_sha256": "abc",
          },
          "image": {
            "y_median": 100.0,
          },
        },
        "cam3": {},
      },
    }

    cameras = acceptance.camera_extract(summary)

    self.assertTrue(cameras["cam2"]["latest_raw_match"])
    self.assertEqual(123, cameras["cam2"]["latest_bytes"])
    self.assertEqual("abc", cameras["cam2"]["raw_sha256"])
    self.assertEqual(100.0, cameras["cam2"]["y_median"])


class VisualArtifactTest(unittest.TestCase):
  def test_file_artifact_hashes_existing_file(self) -> None:
    with TemporaryDirectory() as tmp:
      path = Path(tmp) / "image.jpg"
      path.write_bytes(b"vfe-image")

      artifact = acceptance.file_artifact(path)

      self.assertTrue(artifact["exists"])
      self.assertEqual(len(b"vfe-image"), artifact["bytes"])
      self.assertEqual(64, len(artifact["sha256"]))

  def test_visual_check_artifacts_require_montage_and_snapshot_images(self) -> None:
    with TemporaryDirectory() as tmp:
      root = Path(tmp)
      snapshot_dir = root / "snapshot"
      snapshot_dir.mkdir()
      montage = root / "asius-cams-latest.jpg"
      montage.write_bytes(b"montage")
      for filename in acceptance.VISUAL_SNAPSHOT_FILES.values():
        (snapshot_dir / filename).write_bytes(filename.encode())

      old_montage = acceptance.HOST_MONTAGE
      acceptance.HOST_MONTAGE = montage
      try:
        artifacts = acceptance.visual_check_artifacts(snapshot_dir)
      finally:
        acceptance.HOST_MONTAGE = old_montage

      self.assertTrue(acceptance.visual_check_artifacts_present(artifacts))
      self.assertEqual(str(montage), artifacts["host_montage"]["path"])
      self.assertEqual(64, len(artifacts["snapshot_images"]["cam2_latest"]["sha256"]))

  def test_visual_check_artifacts_fail_when_image_missing(self) -> None:
    self.assertFalse(acceptance.visual_check_artifacts_present({
      "host_montage": {"exists": True, "sha256": "x"},
      "snapshot_images": {},
    }))

  def test_visual_check_hash_matches_reviewed_montage(self) -> None:
    artifacts = {
      "host_montage": {
        "exists": True,
        "sha256": "abc123",
      },
    }

    self.assertTrue(acceptance.visual_check_hash_matches(artifacts, " ABC123 \n"))
    self.assertFalse(acceptance.visual_check_hash_matches(artifacts, "def456"))
    self.assertFalse(acceptance.visual_check_hash_matches(artifacts, ""))
    self.assertFalse(acceptance.visual_check_hash_matches({}, "abc123"))


class FinalAcceptanceSummaryTest(unittest.TestCase):
  def final_ready_summary(self) -> dict:
    return {
      "dry_run": False,
      "passed": True,
      "snapshot": {
        "skipped": False,
        "passed": True,
        "hardware_path_passed": True,
        "raw_vfe_artifacts_passed": True,
        "image_quality_passed": True,
        "profile": "daylight-road",
        "monitor_duration": 130.0,
      },
      "modeld": {
        "skipped": False,
        "passed": True,
        "duration": 130.0,
      },
      "visual_check": {
        "artifacts": {
          "host_montage": {
            "exists": True,
            "sha256": "abc123",
          },
          "snapshot_images": {
            name: {
              "exists": True,
              "sha256": f"{name}-sha",
            }
            for name in acceptance.VISUAL_SNAPSHOT_FILES
          },
        },
      },
      "final_acceptance": {
        "minimum_durations": {
          "snapshot_monitor_duration": 130.0,
          "min_snapshot_monitor_duration": 120.0,
          "modeld_duration": 130.0,
          "min_modeld_duration": 120.0,
        },
      },
    }

  def test_lists_missing_requirements_when_not_final(self) -> None:
    summary = acceptance.final_acceptance_summary({
      "not_dry_run": True,
      "machine_gates_passed": True,
      "snapshot_profile_is_daylight_road": False,
      "snapshot_monitor_duration_long_enough": False,
      "modeld_duration_long_enough": False,
      "visual_check_passed": False,
      "visual_check_scene_is_daylight_road": False,
      "visual_check_note_present": False,
    })

    self.assertFalse(summary["passed"])
    self.assertEqual([
      "snapshot_profile_is_daylight_road",
      "snapshot_monitor_duration_long_enough",
      "modeld_duration_long_enough",
      "visual_check_passed",
      "visual_check_scene_is_daylight_road",
      "visual_check_note_present",
    ], summary["missing_requirements"])

  def test_passes_when_all_requirements_pass(self) -> None:
    summary = acceptance.final_acceptance_summary({
      "not_dry_run": True,
      "machine_gates_passed": True,
      "snapshot_profile_is_daylight_road": True,
      "snapshot_monitor_duration_long_enough": True,
      "modeld_duration_long_enough": True,
      "visual_check_passed": True,
      "visual_check_scene_is_daylight_road": True,
      "visual_check_note_present": True,
    })

    self.assertTrue(summary["passed"])
    self.assertEqual([], summary["missing_requirements"])

  def test_visual_note_requires_non_whitespace_text(self) -> None:
    self.assertFalse(acceptance.final_visual_note_present(""))
    self.assertFalse(acceptance.final_visual_note_present("   \n\t"))
    self.assertTrue(acceptance.final_visual_note_present("daylight road image looks acceptable"))

  def test_apply_visual_review_finalizes_existing_summary_with_matching_hash(self) -> None:
    summary = self.final_ready_summary()

    acceptance.apply_visual_check_review(
      summary,
      True,
      "daylight road image looks acceptable",
      "daylight-road",
      " ABC123 ",
    )

    self.assertTrue(summary["final_acceptance_passed"])
    self.assertTrue(summary["visual_check"]["montage_sha256_matches"])
    self.assertTrue(summary["visual_check"]["artifacts_present"])
    self.assertEqual([], summary["final_acceptance"]["missing_requirements"])
    self.assertIn("reviewed_at", summary["visual_check"])

  def test_apply_visual_review_rejects_wrong_hash(self) -> None:
    summary = self.final_ready_summary()

    acceptance.apply_visual_check_review(
      summary,
      True,
      "daylight road image looks acceptable",
      "daylight-road",
      "def456",
    )

    self.assertFalse(summary["final_acceptance_passed"])
    self.assertIn(
      "visual_check_montage_sha256_matches",
      summary["final_acceptance"]["missing_requirements"],
    )

  def test_apply_visual_review_preserves_legacy_recorded_modeld_duration(self) -> None:
    summary = self.final_ready_summary()
    del summary["modeld"]["duration"]

    acceptance.apply_visual_check_review(
      summary,
      True,
      "daylight road image looks acceptable",
      "daylight-road",
      "abc123",
    )

    self.assertTrue(summary["final_acceptance_passed"])
    self.assertEqual(130.0, summary["final_acceptance"]["minimum_durations"]["modeld_duration"])


if __name__ == "__main__":
  unittest.main()
