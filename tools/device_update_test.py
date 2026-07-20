#!/usr/bin/env python3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import device_update


class DeviceUpdateTest(unittest.TestCase):
  def test_normalize_target_adds_default_user(self):
    self.assertEqual(
      device_update.normalize_target("192.168.88.20"), "comma@192.168.88.20"
    )
    self.assertEqual(device_update.normalize_target("root@example"), "root@example")

  def test_staged_commit_requires_exact_branch_commit_and_consistency(self):
    status = {
      "staged_branch": "one",
      "staged_commit": "a" * 40,
      "staged_consistent": True,
      "update_available": True,
    }
    self.assertTrue(device_update.staged_commit_matches(status, "one", "a" * 40))
    self.assertFalse(device_update.staged_commit_matches(status, "other", "a" * 40))
    self.assertFalse(
      device_update.staged_commit_matches(
        {**status, "staged_consistent": False}, "one", "a" * 40
      )
    )

  def test_validate_checkout_requires_pushed_clean_one(self):
    with tempfile.TemporaryDirectory() as temp_dir:
      checkout = Path(temp_dir)
      (checkout / ".git").mkdir()
      commit = "a" * 40
      with mock.patch.object(
        device_update,
        "git_output",
        side_effect=["one", "", commit, f"{commit}\trefs/heads/one"],
      ):
        self.assertEqual(device_update.validate_checkout(checkout, "one"), commit)

  def test_validate_checkout_rejects_dirty_tree(self):
    with tempfile.TemporaryDirectory() as temp_dir:
      checkout = Path(temp_dir)
      (checkout / ".git").mkdir()
      with mock.patch.object(
        device_update, "git_output", side_effect=["one", " M file"]
      ):
        with self.assertRaisesRegex(
          device_update.DeviceUpdateError, "uncommitted changes"
        ):
          device_update.validate_checkout(checkout, "one")

  def test_main_triggers_updater_and_ignores_old_failure_count(self):
    commit = "a" * 40
    initial = {
      "current_branch": "one",
      "current_commit": "b" * 40,
      "staged_branch": "one",
      "staged_commit": "b" * 40,
      "staged_consistent": True,
      "update_available": True,
      "updater_running": True,
      "updater_state": "idle",
      "update_failed_count": 2,
    }
    finalized = {
      **initial,
      "staged_commit": commit,
      "update_failed_count": 2,
    }
    with (
      mock.patch.object(device_update, "validate_checkout", return_value=commit),
      mock.patch.object(device_update, "ssh_options", return_value=[]),
      mock.patch.object(device_update, "sync_helper"),
      mock.patch.object(
        device_update,
        "remote_call",
        side_effect=[initial, {"triggered": True}, initial, finalized],
      ) as remote_call,
      mock.patch.object(device_update.time, "sleep"),
    ):
      self.assertEqual(device_update.main(["192.168.88.20"]), 0)

    remote_call.assert_any_call(
      "comma@192.168.88.20", [], "/data/openpilot", "trigger", "one"
    )


if __name__ == "__main__":
  unittest.main()
