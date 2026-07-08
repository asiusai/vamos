#!/usr/bin/env python3
"""Focused tests for run_vfe_acceptance.py host-side helpers."""

from __future__ import annotations

import unittest

import run_vfe_acceptance as acceptance


class FinalAcceptanceSummaryTest(unittest.TestCase):
  def test_lists_missing_requirements_when_not_final(self) -> None:
    summary = acceptance.final_acceptance_summary({
      "not_dry_run": True,
      "machine_gates_passed": True,
      "snapshot_profile_is_daylight_road": False,
      "visual_check_passed": False,
      "visual_check_scene_is_daylight_road": False,
    })

    self.assertFalse(summary["passed"])
    self.assertEqual([
      "snapshot_profile_is_daylight_road",
      "visual_check_passed",
      "visual_check_scene_is_daylight_road",
    ], summary["missing_requirements"])

  def test_passes_when_all_requirements_pass(self) -> None:
    summary = acceptance.final_acceptance_summary({
      "not_dry_run": True,
      "machine_gates_passed": True,
      "snapshot_profile_is_daylight_road": True,
      "visual_check_passed": True,
      "visual_check_scene_is_daylight_road": True,
    })

    self.assertTrue(summary["passed"])
    self.assertEqual([], summary["missing_requirements"])


if __name__ == "__main__":
  unittest.main()
