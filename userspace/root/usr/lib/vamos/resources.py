"""Runit resource policy using the native cgroup v2 hierarchy."""

import argparse
import errno
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

CGROUP = Path("/sys/fs/cgroup")
PROC = Path("/proc")
READY = Path("/run/vamos-resources-ready")
ERROR = Path("/run/vamos-resources-error")
CONTROLLERS = {"cpuset", "cpu", "memory", "pids"}
BACKGROUND = {"cpuset.cpus": "0-1", "cpu.weight": "6",
              "memory.max": str(512 * 1024 * 1024), "memory.swap.max": "0", "pids.max": "512"}
SERVICE = {"memory.max": str(128 * 1024 * 1024), "memory.oom.group": "1", "pids.max": "128"}
# Supervisors stay outside service groups. Recovery and explicitly enabled builds
# must also survive a background service exhausting its budget.
UNLIMITED = {"openpilot", "github-runner", "agetty-ttyMSM0", "agetty-ttyAMA0",
             "vamos-boot-commit", "vamos-hypervisor-reboot"}


def write(path: Path, value: str) -> None:
  path.write_text(value)


def configure(path: Path, values: dict[str, str]) -> None:
  path.mkdir(exist_ok=True)
  for name, value in values.items():
    write(path / name, value)


def enable(path: Path) -> None:
  available = set((path / "cgroup.controllers").read_text().split())
  if not CONTROLLERS <= available:
    raise RuntimeError(f"missing cgroup v2 controllers: {sorted(CONTROLLERS - available)}")
  write(path / "cgroup.subtree_control", " ".join("+" + name for name in sorted(CONTROLLERS)))


def configure_groups() -> None:
  enable(CGROUP)
  background = CGROUP / "background"
  configure(background, BACKGROUND)
  enable(background)
  # Empty cpuset.cpus inherits all online CPUs, including cores brought back
  # online after offroad power saving. Never snapshot the current online mask.
  for name in ("workload", "interactive", "openpilot"):
    configure(CGROUP / name, {"cpuset.cpus": "", "cpu.weight": "100"})


def initialize() -> None:
  READY.unlink(missing_ok=True)
  ERROR.unlink(missing_ok=True)
  configure_groups()
  write(PROC / "irq/default_smp_affinity", "3")
  for affinity in (PROC / "irq").glob("[0-9]*/smp_affinity"):
    try:
      write(affinity, "3")
    except OSError as exc:
      # Per-CPU and managed interrupts do not accept userspace affinity.
      if exc.errno not in (errno.EIO, errno.EINVAL, errno.EPERM, errno.ENOENT):
        raise
  READY.touch()


def failure(exc: Exception) -> None:
  message = f"vamos-resources: {exc}"
  print(message, file=sys.stderr, flush=True)
  ERROR.write_text(message + "\n")
  READY.unlink(missing_ok=True)


def healthy() -> None:
  if not READY.exists() or ERROR.exists():
    raise RuntimeError("resource policy is not ready; see /run/vamos-resources-error")
  if not CONTROLLERS <= set((CGROUP / "cgroup.subtree_control").read_text().split()):
    raise RuntimeError("resource controllers are disabled")
  for name, value in BACKGROUND.items():
    if (CGROUP / "background" / name).read_text().strip() != value:
      raise RuntimeError(f"background policy changed: {name}")


def enter(group: Path, pid: int, background: bool = False, protect: bool = False) -> None:
  write(group / "cgroup.procs", str(pid))
  # mq-deadline uses task I/O priorities. BFQ weights are not active with this
  # scheduler, so do not claim or configure proportional cgroup I/O bandwidth.
  subprocess.run(["ionice", "-c", "3" if background else "2", *([] if background else ["-n", "4"]),
                  "-p", str(pid)], check=True)
  write(PROC / str(pid) / "oom_score_adj", "-500" if protect else "0")


def service_group(name: str) -> Path:
  if not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.log)?", name):
    raise ValueError("invalid service name")
  if name.removesuffix(".log") in UNLIMITED:
    return CGROUP / "workload"
  group = CGROUP / "background" / name
  configure(group, SERVICE)
  return group


def prepare(directory: Path) -> None:
  # Wrap the writable runit copy, including distro-provided services. Leave the
  # source run scripts intact and keep runsv outside memory/PID limits.
  for service in sorted(directory.iterdir()):
    if not service.is_dir():
      continue
    for folder, name in ((service, service.name), (service / "log", service.name + ".log")):
      run = folder / "run"
      original = folder / "run-service"
      if not run.is_file() or original.exists():
        continue
      service_group(name)  # Validate names and configure limits before startup.
      run.rename(original)
      run.write_text("#!/bin/sh\nexec /usr/bin/vamos-resources service " + shlex.quote(name) +
                     " " + shlex.quote(str(original)) + ' "$@"\n')
      run.chmod(0o755)


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  commands = parser.add_subparsers(dest="action", required=True)
  for action in ("initialize", "health", "pam-session"):
    commands.add_parser(action)
  commands.add_parser("prepare").add_argument("directory", type=Path)
  service = commands.add_parser("service")
  service.add_argument("name")
  service.add_argument("command", nargs=argparse.REMAINDER)
  commands.add_parser("openpilot").add_argument("command", nargs=argparse.REMAINDER)
  args = parser.parse_args()
  if args.action in ("service", "openpilot") and not args.command:
    parser.error("a command is required")
  try:
    if args.action == "initialize":
      initialize()
    elif args.action == "health":
      healthy()
    elif args.action == "prepare":
      prepare(args.directory)
    elif args.action == "pam-session":
      # pam_exec runs as a child of the authenticated session. Do not move the
      # listening sshd, sudo, or an unrelated caller, and never promote at auth.
      if os.environ.get("PAM_TYPE") == "open_session" and os.environ.get("PAM_SERVICE") in ("sshd", "login"):
        healthy()
        enter(CGROUP / "interactive", os.getppid())
    else:
      healthy()
      group = CGROUP / "openpilot" if args.action == "openpilot" else service_group(args.name)
      enter(group, os.getpid(), group.parent.name == "background", args.action == "openpilot")
  except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
    failure(exc)
    # Keep recovery networking and serial access available after setup failure.
    # The boot health gate refuses trial activation while this error exists.
    if args.action not in ("service", "pam-session"):
      return 1
  if args.action in ("service", "openpilot"):
    os.execvp(args.command[0], args.command)
  return 0
