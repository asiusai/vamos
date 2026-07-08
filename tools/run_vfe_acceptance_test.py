#!/usr/bin/env python3
"""Focused tests for run_vfe_acceptance.py host-side helpers."""

from __future__ import annotations

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


class FinalAcceptanceSummaryTest(unittest.TestCase):
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


if __name__ == "__main__":
  unittest.main()
