#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import contextlib
import json
import lzma
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "userspace/root/usr/lib"))

from vamos import update
from vamos import boot


class ManifestTest(unittest.TestCase):
  def test_relative_manifest_sources(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      directory = Path(temporary)
      manifest_path = directory / "vamos.json"
      manifest_path.write_text(json.dumps({
        "manifest_version": 1,
        "minimum_updater_version": 1,
        "product": update.PRODUCT,
        "version": "test-1",
        "partitions": [
          {"name": "esp", "url": "esp.img.xz", "size": 32, "sha256": "a" * 64, "compression": "xz"},
          {"name": "system", "url": "system.img.xz", "size": 64, "sha256": "b" * 64, "compression": "xz"},
        ],
      }))
      with mock.patch.object(update, "ESP_SIZE", 32), mock.patch.object(update, "SYSTEM_SIZE", 64):
        manifest = update.load_manifest(str(manifest_path))

      self.assertEqual(manifest.version, "test-1")
      self.assertEqual(manifest.images[0].source, (directory / "esp.img.xz").as_uri())
      self.assertEqual(manifest.images[1].source, (directory / "system.img.xz").as_uri())

  def test_rejects_wrong_product_and_duplicate_partition(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      path = Path(temporary) / "bad.json"
      path.write_text(json.dumps({
        "manifest_version": 1,
        "product": "wrong-board",
        "partitions": [],
      }))
      with self.assertRaises(update.UpdateError):
        update.load_manifest(str(path))

  def test_local_directory_with_raw_and_xz_images(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      directory = Path(temporary)
      esp = b"E" * 32
      system = b"S" * 64
      (directory / "esp.img").write_bytes(esp)
      with lzma.open(directory / "system.img.xz", "wb") as output:
        output.write(system)
      (directory / "VERSION").write_text("local-test\n")

      with mock.patch.object(update, "ESP_SIZE", len(esp)), mock.patch.object(update, "SYSTEM_SIZE", len(system)):
        manifest = update.manifest_from_directory(directory)

      self.assertEqual(manifest.version, "local-test")
      self.assertEqual(manifest.images[0].sha256, hashlib.sha256(esp).hexdigest())
      self.assertEqual(manifest.images[1].sha256, hashlib.sha256(system).hexdigest())

  def test_remote_manifest_requires_valid_signature(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      directory = Path(temporary)
      private_key = directory / "private.pem"
      public_key = directory / "public.pem"
      manifest_path = directory / "vamos.json"
      signature_path = directory / "vamos.json.sig"
      payload = json.dumps({
        "manifest_version": 1,
        "minimum_updater_version": 1,
        "product": update.PRODUCT,
        "version": "signed-test",
        "partitions": [
          {"name": "esp", "url": "esp.img.xz", "size": 32, "sha256": "a" * 64, "compression": "xz"},
          {"name": "system", "url": "system.img.xz", "size": 64, "sha256": "b" * 64, "compression": "xz"},
        ],
      }).encode()
      manifest_path.write_bytes(payload)
      subprocess.run(["openssl", "genpkey", "-algorithm", "Ed25519", "-out", private_key], check=True)
      subprocess.run(["openssl", "pkey", "-in", private_key, "-pubout", "-out", public_key], check=True)
      subprocess.run([
        "openssl", "pkeyutl", "-sign", "-inkey", private_key, "-rawin",
        "-in", manifest_path, "-out", signature_path,
      ], check=True)
      signature = signature_path.read_bytes()

      with (
        mock.patch.object(update, "UPDATE_PUBLIC_KEY", public_key),
        mock.patch.object(update, "ESP_SIZE", 32),
        mock.patch.object(update, "SYSTEM_SIZE", 64),
        mock.patch.object(update, "_read_url_bytes", side_effect=[payload, signature]),
      ):
        manifest = update.load_manifest("https://updates.example/vamos.json")
      self.assertEqual(manifest.version, "signed-test")

      with (
        mock.patch.object(update, "UPDATE_PUBLIC_KEY", public_key),
        mock.patch.object(update, "_read_url_bytes", side_effect=[payload + b" ", signature]),
      ):
        with self.assertRaises(update.UpdateError):
          update.load_manifest("https://updates.example/vamos.json")


class StateFileTest(unittest.TestCase):
  def test_atomic_state_is_readable_by_device_services(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      state_file = Path(temporary) / "state.json"
      update.atomic_write_json(state_file, {"state": "writing"})
      self.assertEqual(state_file.stat().st_mode & 0o777, 0o644)


class LayoutInitializationTest(unittest.TestCase):
  def test_layout_is_marked_ready_only_after_boot_control_finishes(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      directory = Path(temporary)
      selector = directory / "BOOTAA64.EFI"
      ready_marker = directory / ".vamos-ab-ready"
      selector.write_bytes(b"selector")
      with (
        mock.patch.object(update, "BOOT_SELECTOR_SOURCE", selector),
        mock.patch.object(update, "LAYOUT_READY_MARKER", ready_marker),
        mock.patch.object(update, "current_slot", return_value="a"),
        mock.patch.object(update, "root_reference", side_effect=lambda slot: f"PARTLABEL=rootfs_{slot}"),
        mock.patch.object(update, "_install_selector_on_slot"),
        mock.patch.object(update, "set_boot_control", return_value=4) as set_control,
        mock.patch.object(update.os.path, "ismount", return_value=True),
        mock.patch.object(update.os, "sync"),
      ):
        update._finish_layout_initialization()

      set_control.assert_called_once_with("a")
      self.assertTrue(ready_marker.is_file())

  def test_compact_image_gpt_is_relocated_to_physical_disk_end(self) -> None:
    table = {"lastlba": 100}
    relocated = {"lastlba": 966}

    def fake_run(command, **kwargs):
      if command[:2] == ["blockdev", "--getsz"]:
        return subprocess.CompletedProcess(command, 0, "1000\n", "")
      return subprocess.CompletedProcess(command, 0, "", "")

    with (
      mock.patch.object(update, "run", side_effect=fake_run) as run,
      mock.patch.object(update, "_layout_json", return_value=relocated) as layout_json,
    ):
      self.assertIs(update._relocate_backup_gpt(table), relocated)

    run.assert_any_call([
      "sfdisk", "--no-reread", "--relocate", "gpt-bak-std", str(update.DISK),
    ], capture=False)
    run.assert_any_call(["blockdev", "--rereadpt", str(update.DISK)], check=False, capture=False)
    layout_json.assert_called_once_with()

  def test_userdata_rsync_tolerates_files_vanishing(self) -> None:
    result = subprocess.CompletedProcess(["rsync"], 24)
    with mock.patch.object(update, "run", return_value=result) as run:
      update._rsync_userdata(Path("/source"), Path("/destination"))

    command = run.call_args.args[0]
    self.assertIn("--exclude=.tmp_value_*", command)

  def test_userdata_rsync_rejects_other_errors(self) -> None:
    result = subprocess.CompletedProcess(["rsync"], 23)
    with mock.patch.object(update, "run", return_value=result):
      with self.assertRaises(update.UpdateError):
        update._rsync_userdata(Path("/source"), Path("/destination"))

  def test_incomplete_ab_layout_resumes_userdata_migration(self) -> None:
    table = {
      "partitions": [
        {"name": "esp_a"},
        {"name": "rootfs_a"},
        {"name": "esp_b"},
        {"name": "rootfs_b"},
        {"name": "userdata"},
      ],
    }
    with tempfile.TemporaryDirectory() as temporary:
      marker = Path(temporary) / ".vamos-userdata"
      with (
        mock.patch.object(update.os, "geteuid", return_value=0),
        mock.patch.object(update, "USERDATA_MARKER", marker),
        mock.patch.object(update, "_layout_json", return_value=table),
        mock.patch.object(update, "_ensure_incomplete_layout_filesystems") as ensure_filesystems,
        mock.patch.object(update, "_migrate_userdata") as migrate,
        mock.patch.object(update, "_finish_layout_initialization") as finish,
      ):
        update.initialize_layout(confirm=True)

    ensure_filesystems.assert_called_once_with()
    migrate.assert_called_once_with(Path(f"{update.DISK}p5"))
    finish.assert_called_once_with()

  def test_incomplete_layout_only_formats_missing_filesystems(self) -> None:
    results = {
      str(update.DISK) + "p3": "vfat\n",
      str(update.DISK) + "p4": "ext4\n",
      str(update.DISK) + "p5": "",
    }

    def fake_run(command, **kwargs):
      if command[0] == "blkid":
        return subprocess.CompletedProcess(command, 0, results[command[-1]], "")
      return subprocess.CompletedProcess(command, 0, "", "")

    with mock.patch.object(update, "run", side_effect=fake_run) as run:
      update._ensure_incomplete_layout_filesystems()

    commands = [call.args[0] for call in run.call_args_list]
    self.assertIn(["mkfs.ext4", "-F", "-L", "VAMOS-DATA", str(update.DISK) + "p5"], commands)
    self.assertNotIn(["mkfs.vfat", "-F", "32", "-n", "VAMOS-B", str(update.DISK) + "p3"], commands)


class ImageWriteTest(unittest.TestCase):
  def test_write_and_verify_xz_image(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      directory = Path(temporary)
      raw = bytes(range(256)) * 64
      source = directory / "system.img.xz"
      destination = directory / "target"
      destination.write_bytes(b"\xff" * len(raw))
      with lzma.open(source, "wb") as output:
        output.write(raw)
      spec = update.ImageSpec("system", source.as_uri(), len(raw), hashlib.sha256(raw).hexdigest(), "xz")

      progress: list[tuple[str, int]] = []
      with mock.patch.object(update, "block_size", return_value=len(raw)):
        update.write_image(spec, destination, progress_callback=lambda phase, percent: progress.append((phase, percent)))

      self.assertEqual(destination.read_bytes(), raw)
      self.assertIn(("writing", 100), progress)
      self.assertIn(("verifying", 100), progress)

  def test_hash_mismatch_is_rejected(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      directory = Path(temporary)
      source = directory / "esp.img"
      destination = directory / "target"
      source.write_bytes(b"source")
      destination.write_bytes(b"target")
      spec = update.ImageSpec("esp", source.as_uri(), 6, "0" * 64, "none")

      with mock.patch.object(update, "block_size", return_value=6):
        with self.assertRaises(update.UpdateError):
          update.write_image(spec, destination)

  def test_rejects_non_arm64_efi_image(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      image = Path(temporary) / "kernel"
      header = bytearray(4096)
      header[:2] = b"MZ"
      header[0x3c:0x40] = (128).to_bytes(4, "little")
      header[128:132] = b"PE\0\0"
      header[132:134] = (0x8664).to_bytes(2, "little")
      image.write_bytes(header)
      with self.assertRaises(update.UpdateError):
        update.verify_arm64_efi(image)

      header[132:134] = (0xaa64).to_bytes(2, "little")
      image.write_bytes(header)
      update.verify_arm64_efi(image)


class ActivationSafetyTest(unittest.TestCase):
  def setUp(self) -> None:
    self.temporary = tempfile.TemporaryDirectory()
    self.directory = Path(self.temporary.name)
    self.state_file = self.directory / "state.json"
    self.history_file = self.directory / "history.jsonl"
    self.manifest = update.Manifest(
      "test-2",
      (
        update.ImageSpec("esp", "file:///esp", 32, "a" * 64, "none"),
        update.ImageSpec("system", "file:///system", 64, "b" * 64, "none"),
      ),
      "file:///vamos.json",
    )
    self.state_patches = (
      mock.patch.object(update, "STATE_DIR", self.directory),
      mock.patch.object(update, "STATE_FILE", self.state_file),
      mock.patch.object(update, "HISTORY_FILE", self.history_file),
    )
    for patcher in self.state_patches:
      patcher.start()

  def tearDown(self) -> None:
    for patcher in reversed(self.state_patches):
      patcher.stop()
    self.temporary.cleanup()

  def test_activation_only_occurs_after_both_images_verify(self) -> None:
    writes: list[str] = []
    with (
      mock.patch.object(update.os, "geteuid", return_value=0),
      mock.patch.object(update, "verify_layout"),
      mock.patch.object(update, "current_slot", return_value="a"),
      mock.patch.object(update, "partition_path", side_effect=lambda slot, name: Path(f"/{slot}/{name}")),
      mock.patch.object(update, "selected_boot_control", return_value={"generation": 1}),
      mock.patch.object(update, "rollback_boot", return_value=2) as rollback,
      mock.patch.object(update, "write_image", side_effect=lambda spec, destination, progress_callback=None: writes.append(spec.name)),
      mock.patch.object(update, "prepare_disk_selector", return_value=3) as prepare_selector,
      mock.patch.object(update, "verify_system_contents"),
      mock.patch.object(update, "verify_esp_contents"),
      mock.patch.object(update, "activate_staged", return_value={"state": "ready"}) as activate,
    ):
      state = update.install(self.manifest)

    self.assertEqual(writes, ["system", "esp"])
    rollback.assert_called_once_with("a", "b")
    prepare_selector.assert_called_once_with("a", "b")
    activate.assert_called_once_with(reboot=False)
    self.assertEqual(state["state"], "ready")

  def test_deferred_install_never_arms_trial(self) -> None:
    with (
      mock.patch.object(update.os, "geteuid", return_value=0),
      mock.patch.object(update, "verify_layout"),
      mock.patch.object(update, "current_slot", return_value="a"),
      mock.patch.object(update, "partition_path", side_effect=lambda slot, name: Path(f"/{slot}/{name}")),
      mock.patch.object(update, "selected_boot_control", return_value={"generation": 1}),
      mock.patch.object(update, "rollback_boot", return_value=2),
      mock.patch.object(update, "write_image"),
      mock.patch.object(update, "prepare_disk_selector", return_value=3),
      mock.patch.object(update, "verify_system_contents"),
      mock.patch.object(update, "verify_esp_contents"),
      mock.patch.object(update, "activate_staged") as activate,
    ):
      state = update.install(self.manifest, activate=False)

    activate.assert_not_called()
    self.assertEqual(state["state"], "verified")

  def test_activate_rechecks_images_before_arming_trial(self) -> None:
    self.state_file.write_text(json.dumps({
      "state": "verified",
      "version": "test-2",
      "active_slot": "a",
      "target_slot": "b",
      "images": {
        "esp": {"size": 32, "sha256": "a" * 64},
        "system": {"size": 64, "sha256": "b" * 64},
      },
    }))
    calls: list[str] = []
    with (
      mock.patch.object(update.os, "geteuid", return_value=0),
      mock.patch.object(update, "verify_layout"),
      mock.patch.object(update, "current_slot", return_value="a"),
      mock.patch.object(update, "partition_path", side_effect=lambda slot, name: Path(f"/{slot}/{name}")),
      mock.patch.object(update, "verify_installed", side_effect=lambda: calls.append("hash") or True),
      mock.patch.object(update, "verify_system_contents", side_effect=lambda *args: calls.append("system")),
      mock.patch.object(update, "verify_esp_contents", side_effect=lambda *args: calls.append("esp")),
      mock.patch.object(update, "prepare_trial_boot", side_effect=lambda *args: calls.append("arm") or 4),
    ):
      state = update.activate_staged()

    self.assertEqual(calls, ["hash", "system", "esp", "arm"])
    self.assertEqual(state["state"], "ready")

  def test_failed_image_never_arms_trial(self) -> None:
    def fail_esp(spec: update.ImageSpec, destination: Path, progress_callback=None) -> None:
      if spec.name == "esp":
        raise update.UpdateError("injected image failure")

    with (
      mock.patch.object(update.os, "geteuid", return_value=0),
      mock.patch.object(update, "verify_layout"),
      mock.patch.object(update, "current_slot", return_value="a"),
      mock.patch.object(update, "partition_path", side_effect=lambda slot, name: Path(f"/{slot}/{name}")),
      mock.patch.object(update, "selected_boot_control", return_value={"generation": 1}),
      mock.patch.object(update, "rollback_boot", return_value=2) as rollback,
      mock.patch.object(update, "write_image", side_effect=fail_esp),
      mock.patch.object(update, "activate_staged") as activate,
    ):
      with self.assertRaises(update.UpdateError):
        update.install(self.manifest)

    activate.assert_not_called()
    self.assertEqual(rollback.call_count, 2)
    self.assertEqual(json.loads(self.state_file.read_text())["state"], "failed")


class BootControlSafetyTest(unittest.TestCase):
  def control(self, **overrides) -> dict[str, str | int]:
    values: dict[str, str | int] = {
      "generation": 7,
      "active": "a",
      "pending": "b",
      "phase": "armed",
      "root_a": "PARTUUID=1111-aaaa",
      "root_b": "PARTUUID=2222-bbbb",
    }
    values.update(overrides)
    return values

  def test_environment_round_trip_and_validation(self) -> None:
    payload = update.encode_boot_control(self.control())
    self.assertEqual(len(payload), 1024)
    self.assertEqual(update.decode_boot_control(payload), self.control())

    with self.assertRaises(update.UpdateError):
      update.encode_boot_control(self.control(root_a="/dev/nvme0n1p2"))
    with self.assertRaises(update.UpdateError):
      update.decode_boot_control(payload[:-1])

  def test_redundant_state_selection_prefers_conservative_tie(self) -> None:
    controls = [
      self.control(phase="armed"),
      self.control(phase="attempted"),
      self.control(generation=6, phase="stable", pending=""),
    ]
    with mock.patch.object(update, "read_boot_controls", return_value=controls):
      selected = update.selected_boot_control()
    self.assertEqual(selected, controls[1])

  def test_set_boot_control_writes_both_esps(self) -> None:
    with (
      mock.patch.object(update, "selected_boot_control", return_value=self.control()),
      mock.patch.object(update, "root_reference", side_effect=lambda slot: f"PARTLABEL=rootfs_{slot}"),
      mock.patch.object(update, "_write_control_to_slot", return_value=True) as write,
    ):
      generation = update.set_boot_control("a", "b", "armed")
    self.assertEqual(generation, 8)
    self.assertEqual([call.args[0] for call in write.call_args_list], ["a", "b"])
    for call in write.call_args_list:
      control = update.decode_boot_control(call.args[1])
      self.assertEqual(control["phase"], "armed")

  def test_selector_migration_preserves_running_kernel(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      esp = Path(temporary)
      kernel = bytearray(4096)
      kernel[:2] = b"MZ"
      kernel[0x3c:0x40] = (128).to_bytes(4, "little")
      kernel[128:132] = b"PE\0\0"
      kernel[132:134] = (0xaa64).to_bytes(2, "little")
      selector = bytes(kernel) + b"selector"
      (esp / "Image").write_bytes(kernel)
      control = update.encode_boot_control(self.control(phase="stable", pending=""))

      with (
        mock.patch.object(update, "mounted_esp", return_value=contextlib.nullcontext(esp)),
        mock.patch.object(update, "run"),
        mock.patch.object(update.os, "sync"),
      ):
        update._install_selector_on_slot("a", selector, control, device=Path("/esp_a"))

      self.assertEqual((esp / update.ESP_KERNEL).read_bytes(), bytes(kernel))
      self.assertEqual((esp / update.ESP_PRIMARY_LOADER).read_bytes(), selector)
      self.assertEqual((esp / update.ESP_RECOVERY_LOADER).read_bytes(), selector)
      self.assertEqual(update.decode_boot_control((esp / update.BOOT_CONTROL).read_bytes())["phase"], "stable")


class BootSafetyTest(unittest.TestCase):
  def setUp(self) -> None:
    self.temporary = tempfile.TemporaryDirectory()
    self.directory = Path(self.temporary.name)
    self.paths = {
      "TRIAL_MARKER": self.directory / "trial",
      "WATCHDOG_READY_MARKER": self.directory / "ready",
      "WATCHDOG_DISARMED_MARKER": self.directory / "disarmed",
      "HEALTHY_MARKER": self.directory / "healthy",
      "STAGE1_MARKER": self.directory / "stage1",
      "WATCHDOG_PID_FILE": self.directory / "watchdog.pid",
      "WATCHDOG_LOG": self.directory / "watchdog.log",
      "WATCHDOG_DEVICE": self.directory / "watchdog0",
      "VERSION_FILE": self.directory / "VERSION",
    }
    self.patchers = [mock.patch.object(boot, name, path) for name, path in self.paths.items()]
    for patcher in self.patchers:
      patcher.start()
    self.paths["TRIAL_MARKER"].touch()
    self.paths["STAGE1_MARKER"].touch()
    self.paths["VERSION_FILE"].write_text("test-3\n")

  def tearDown(self) -> None:
    for patcher in reversed(self.patchers):
      patcher.stop()
    self.temporary.cleanup()

  def test_commit_rejects_missing_watchdog(self) -> None:
    with mock.patch.object(boot, "commit_boot") as commit_boot:
      with self.assertRaises(update.UpdateError):
        boot.commit()
    commit_boot.assert_not_called()

  def test_watchdog_launcher_uses_absolute_python_path(self) -> None:
    self.paths["TRIAL_MARKER"].unlink()

    class Process:
      pid = 123

      @staticmethod
      def poll():
        return None

    def mark_ready(_seconds: float) -> None:
      self.paths["WATCHDOG_READY_MARKER"].touch()

    with (
      mock.patch.object(boot, "is_trial_boot", return_value=True),
      mock.patch.object(boot.subprocess, "Popen", return_value=Process()) as popen,
      mock.patch.object(boot.time, "sleep", side_effect=mark_ready),
    ):
      boot.start_watchdog()

    self.assertEqual(popen.call_args.args[0], ["/usr/bin/python3", "/usr/bin/vamos-boot", "watchdog"])

  def test_watchdog_retries_until_device_appears(self) -> None:
    self.paths["HEALTHY_MARKER"].touch()
    self.paths["WATCHDOG_DEVICE"].touch()
    real_open = os.open
    attempts = 0

    def delayed_open(path, flags, mode=0o777):
      nonlocal attempts
      if Path(path) == self.paths["WATCHDOG_DEVICE"]:
        attempts += 1
        if attempts == 1:
          raise FileNotFoundError
      return real_open(path, flags, mode)

    with (
      mock.patch.object(boot.os, "open", side_effect=delayed_open),
      mock.patch.object(boot.fcntl, "ioctl"),
      mock.patch.object(boot.time, "monotonic", side_effect=[0.0, 0.0, 1.0]),
      mock.patch.object(boot.time, "sleep"),
    ):
      boot.watchdog()

    self.assertEqual(attempts, 2)
    self.assertTrue(self.paths["WATCHDOG_DISARMED_MARKER"].exists())

  def test_watchdog_disarms_before_boot_control_commit(self) -> None:
    self.paths["WATCHDOG_READY_MARKER"].touch()
    self.paths["WATCHDOG_PID_FILE"].write_text(f"{__import__('os').getpid()}\n")

    def disarm(_seconds: float) -> None:
      self.paths["WATCHDOG_DISARMED_MARKER"].touch()

    def check_order(active: str, previous: str) -> int:
      self.assertTrue(self.paths["HEALTHY_MARKER"].exists())
      self.assertTrue(self.paths["WATCHDOG_DISARMED_MARKER"].exists())
      return 9

    state = {
      "state": "booting",
      "version": "test-3",
      "target_slot": "b",
      "active_slot": "a",
    }
    with (
      mock.patch.object(boot.os.path, "ismount", return_value=True),
      mock.patch.object(boot, "current_slot", return_value="b"),
      mock.patch.object(boot, "load_state", return_value=state),
      mock.patch.object(boot.time, "sleep", side_effect=disarm),
      mock.patch.object(boot, "commit_boot", side_effect=check_order) as commit_boot,
      mock.patch.object(boot, "save_state") as save_state,
      mock.patch.object(boot.os, "sync"),
    ):
      boot.commit()

    commit_boot.assert_called_once_with("b", "a")
    self.assertEqual(state["state"], "committed")
    self.assertEqual(state["active_slot"], "b")
    self.assertEqual(state["previous_slot"], "a")
    self.assertEqual(state["boot_generation"], 9)
    self.assertFalse(self.paths["TRIAL_MARKER"].exists())
    save_state.assert_called_once_with(state, "committed")


if __name__ == "__main__":
  unittest.main()
