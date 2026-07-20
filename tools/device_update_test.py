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

  def test_validate_image_rejects_wrong_size(self):
    with tempfile.TemporaryDirectory() as temp_dir:
      image = Path(temp_dir) / "esp.img"
      image.write_bytes(b"wrong")
      with mock.patch.dict(device_update.IMAGE_SIZES, {"esp": 10}):
        with self.assertRaisesRegex(device_update.DeviceUpdateError, "expected 10"):
          device_update.validate_image(image, "esp")

  def test_compress_image_reuses_content_addressed_ota_file(self):
    with tempfile.TemporaryDirectory() as temp_dir:
      build_dir = Path(temp_dir)
      ota_dir = build_dir / "ota"
      ota_dir.mkdir()
      cached = ota_dir / "system-abc.img.xz"
      cached.write_bytes(b"compressed")

      result = device_update.compress_image(
        "system",
        build_dir / "system.img",
        "abc",
        build_dir,
        build_dir / "device-update",
      )
      self.assertEqual(result, cached)

  def test_preflight_requires_data_directory(self):
    with self.assertRaisesRegex(device_update.DeviceUpdateError, "below /data"):
      device_update.preflight_remote("comma@example", [], "/tmp/vamos-local")

  def test_main_rsyncs_local_payload_and_installs_even_same_version(self):
    with tempfile.TemporaryDirectory() as temp_dir:
      root = Path(temp_dir)
      system = root / "system.img.xz"
      esp = root / "esp.img.xz"
      version = root / "VERSION"
      updater = root / "update.py"
      for path in (system, esp, version, updater):
        path.write_bytes(b"x")
      payload = device_update.Payload(
        "18.1",
        version,
        updater,
        (
          device_update.PreparedImage("system", root / "system.img", system, "a"),
          device_update.PreparedImage("esp", root / "esp.img", esp, "b"),
        ),
      )

      with (
        mock.patch.object(device_update, "ssh_options", return_value=[]),
        mock.patch.object(device_update, "prepare_payload", return_value=payload),
        mock.patch.object(device_update, "preflight_remote") as preflight,
        mock.patch.object(device_update, "sync_payload") as sync,
        mock.patch.object(device_update, "install_remote") as install,
      ):
        self.assertEqual(device_update.main(["192.168.88.20"]), 0)

      preflight.assert_called_once_with(
        "comma@192.168.88.20", [], device_update.DEFAULT_REMOTE_DIR
      )
      sync.assert_called_once_with(
        payload,
        "comma@192.168.88.20",
        [],
        device_update.DEFAULT_REMOTE_DIR,
      )
      install.assert_called_once_with(
        "comma@192.168.88.20",
        [],
        device_update.DEFAULT_REMOTE_DIR,
      )

  def test_install_uses_local_updater_without_version_gate(self):
    with mock.patch.object(device_update, "remote_command") as remote:
      device_update.install_remote(
        "comma@example",
        ["-o", "BatchMode=yes"],
        "/data/vamos-local",
      )

    self.assertEqual(
      remote.call_args_list,
      [
        mock.call(
          "comma@example",
          ["-o", "BatchMode=yes"],
          [
            "sudo",
            "-n",
            "python3",
            "/data/vamos-local/update.py",
            "local",
            "/data/vamos-local",
          ],
        ),
        mock.call(
          "comma@example",
          ["-o", "BatchMode=yes"],
          ["sudo", "-n", "reboot"],
        ),
      ],
    )

  def test_install_accepts_expected_ssh_disconnect_during_reboot(self):
    disconnect = device_update.subprocess.CalledProcessError(255, ["ssh"])
    with mock.patch.object(
      device_update,
      "remote_command",
      side_effect=[None, disconnect],
    ):
      device_update.install_remote(
        "comma@example",
        [],
        "/data/vamos-local",
      )


if __name__ == "__main__":
  unittest.main()
