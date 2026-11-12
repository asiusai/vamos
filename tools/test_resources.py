import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "userspace/root/usr/lib/vamos/resources.py"
spec = importlib.util.spec_from_file_location("resources", MODULE_PATH)
resources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resources)


class ResourcePolicyTest(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.root = Path(temporary.name)
    for name, path in {"CGROUP": self.root / "cgroup", "PROC": self.root / "proc",
                       "READY": self.root / "ready", "ERROR": self.root / "error"}.items():
      patcher = patch.object(resources, name, path)
      patcher.start()
      self.addCleanup(patcher.stop)
    resources.CGROUP.mkdir()

  def test_missing_controller_is_an_error(self):
    (resources.CGROUP / "cgroup.controllers").write_text("cpu io memory pids")
    with self.assertRaisesRegex(RuntimeError, "cpuset"):
      resources.configure_groups()

  def test_service_wrapping_preserves_arguments_and_is_idempotent(self):
    directory = self.root / "services"
    service = directory / "dnsmasq"
    (service / "log").mkdir(parents=True)
    for folder in (service, service / "log"):
      (folder / "run").write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
      (folder / "run").chmod(0o755)
    with patch.object(resources, "service_group"):
      resources.prepare(directory)
      first = (service / "run").read_text()
      resources.prepare(directory)
    self.assertEqual((service / "run").read_text(), first)
    self.assertIn("dnsmasq.log", (service / "log/run").read_text())
    self.assertEqual(subprocess.check_output([service / "run-service", "two words", "$(false)"], text=True),
                     "two words\n$(false)\n")

  def test_invalid_name_cannot_escape_group(self):
    for name in ("../openpilot", "a/b", "bad;command", ""):
      with self.subTest(name=name), self.assertRaises(ValueError):
        resources.service_group(name)

  def test_login_promotion_only_at_authenticated_session(self):
    for service, phase, promoted in (("sshd", "open_session", True), ("login", "open_session", True),
                                      ("sshd", "auth", False), ("sshd", "close_session", False),
                                      ("sudo", "open_session", False)):
      with self.subTest(service=service, phase=phase), patch.dict(os.environ, PAM_SERVICE=service, PAM_TYPE=phase), \
           patch("sys.argv", ["vamos-resources", "pam-session"]), patch.object(resources, "healthy"), \
           patch.object(resources, "enter") as enter:
        self.assertEqual(resources.main(), 0)
        self.assertEqual(enter.called, promoted)
        if promoted:
          enter.assert_called_once_with(resources.CGROUP / "interactive", os.getppid())

  def test_failed_policy_keeps_recovery_but_blocks_workload_and_health(self):
    for command, executes in ((["service", "sshd", "/bin/true"], True), (["openpilot", "/bin/true"], False)):
      with self.subTest(command=command), patch("sys.argv", ["vamos-resources", *command]), \
           patch.object(resources, "healthy", side_effect=RuntimeError("injected policy failure")), \
           patch.object(resources.os, "execvp") as execute:
        resources.READY.touch()
        resources.main()
        self.assertEqual(execute.called, executes)
        self.assertFalse(resources.READY.exists())
        self.assertIn("injected policy failure", resources.ERROR.read_text())
    with self.assertRaisesRegex(RuntimeError, "not ready"):
      resources.healthy()


if __name__ == "__main__":
  unittest.main()
