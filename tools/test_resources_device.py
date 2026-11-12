"""Run explicitly as root on an idle Dragon; use only a disposable cgroup tree."""

import importlib.util
import json
import os
import signal
import subprocess
import sys
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "userspace/root/usr/lib/vamos/resources.py"
spec = importlib.util.spec_from_file_location("resources", MODULE)
resources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resources)
ROOT = Path("/sys/fs/cgroup")
TEST_ROOT = ROOT / "vamos-resource-test"


@unittest.skipUnless(os.geteuid() == 0 and Path("/ASIUS").exists(), "requires an idle Dragon and root")
class ResourceKernelTest(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    if TEST_ROOT.exists():
      raise RuntimeError("test cgroup already exists; inspect it before retrying")
    cls.original = set((ROOT / "cgroup.subtree_control").read_text().split())
    resources.enable(ROOT)
    TEST_ROOT.mkdir()
    cls.addClassCleanup(cls.cleanup_groups)
    resources.CGROUP = TEST_ROOT
    resources.configure_groups()

  @classmethod
  def cleanup_groups(cls):
    (TEST_ROOT / "cgroup.kill").write_text("1")
    # All subprocesses are waited for by each test before removing their groups.
    for directory in sorted(TEST_ROOT.rglob("*"), key=lambda p: len(p.parts), reverse=True):
      if directory.is_dir():
        directory.rmdir()
    TEST_ROOT.rmdir()
    added = resources.CONTROLLERS - cls.original
    if added:
      (ROOT / "cgroup.subtree_control").write_text(" ".join("-" + value for value in sorted(added)))

  def launch(self, group, body):
    code = ("import importlib.util,os,json,subprocess,errno\nfrom pathlib import Path\n"
            f"s=importlib.util.spec_from_file_location('r',{str(MODULE)!r});r=importlib.util.module_from_spec(s);s.loader.exec_module(r)\n"
            f"r.enter(Path({str(group)!r}),os.getpid(),{group.parent.name == 'background'!r})\n" + body)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=25, check=False)

  def test_cpu_and_io_policy_is_inherited_by_children(self):
    group = resources.service_group("inheritance")
    result = self.launch(group, "print(json.dumps(sorted(os.sched_getaffinity(0))))\n"
                         "subprocess.run(['ionice','-p',str(os.getpid())],check=True)\n"
                         "subprocess.run(['cat','/proc/self/cgroup'],check=True)\n")
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn("[0, 1]", result.stdout)
    self.assertIn("idle", result.stdout)
    self.assertIn("/vamos-resource-test/background/inheritance", result.stdout)

  def test_foreground_inherits_expanded_cpu_set(self):
    (TEST_ROOT / "cpuset.cpus").write_text("0-1")
    try:
      group = TEST_ROOT / "interactive"
      self.assertEqual((group / "cpuset.cpus.effective").read_text().strip(), "0-1")
      (TEST_ROOT / "cpuset.cpus").write_text("0-3")
      result = self.launch(group, "print(json.dumps(sorted(os.sched_getaffinity(0))))")
      self.assertEqual(result.returncode, 0, result.stderr)
      self.assertEqual(json.loads(result.stdout), [0, 1, 2, 3])
    finally:
      (TEST_ROOT / "cpuset.cpus").write_text("")

  def test_memory_failure_is_contained_and_service_can_restart(self):
    group = resources.service_group("memory-test")
    result = self.launch(group, "data=bytearray(160*1024*1024)\nprint(len(data))")
    self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
    events = dict(line.split() for line in (group / "memory.events").read_text().splitlines())
    self.assertGreater(int(events["oom_kill"]), 0)
    restarted = self.launch(group, "print('restarted')")
    self.assertEqual(restarted.returncode, 0, restarted.stderr)
    self.assertEqual(restarted.stdout.strip(), "restarted")

  def test_task_limit_rejects_fork_without_affecting_parent(self):
    group = resources.service_group("tasks-test")
    result = self.launch(group, "children=[]\ntry:\n"
                         " for i in range(160):\n"
                         "  try: children.append(subprocess.Popen(['/bin/sleep','20']))\n"
                         "  except OSError as e:\n"
                         "   assert e.errno == errno.EAGAIN\n   print('bounded',len(children));break\n"
                         " else: raise AssertionError('task limit not enforced')\n"
                         "finally:\n for p in children:p.terminate()\n for p in children:p.wait()\n")
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn("bounded 127", result.stdout)
    self.assertGreater(int((group / "pids.events").read_text().split()[1]), 0)


if __name__ == "__main__":
  unittest.main()
