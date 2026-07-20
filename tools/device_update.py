#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path


VAMOS_ROOT = Path(__file__).resolve().parents[1]
REMOTE_HELPER = "/tmp/vamos-device-update.py"
SSH_HOSTKEY_OPTIONS = (
  "BatchMode=yes",
  "ConnectTimeout=10",
  "ServerAliveInterval=5",
  "ServerAliveCountMax=2",
  "StrictHostKeyChecking=no",
  "UserKnownHostsFile=/dev/null",
  "GlobalKnownHostsFile=/dev/null",
  "LogLevel=ERROR",
)


class DeviceUpdateError(RuntimeError):
  pass


def command_output(command: list[str], cwd: Path | None = None) -> str:
  try:
    return subprocess.check_output(
      command, cwd=cwd, stderr=subprocess.STDOUT, text=True
    ).strip()
  except subprocess.CalledProcessError as error:
    raise DeviceUpdateError(
      error.output.strip() or f"command failed: {shlex.join(command)}"
    ) from error


def git_output(openpilot: Path, *args: str) -> str:
  return command_output(["git", *args], cwd=openpilot)


def normalize_target(target: str) -> str:
  return target if "@" in target else f"comma@{target}"


def validate_checkout(openpilot: Path, branch: str) -> str:
  if not (openpilot / ".git").exists():
    raise DeviceUpdateError(f"openpilot checkout not found at {openpilot}")
  current_branch = git_output(openpilot, "branch", "--show-current")
  if current_branch != branch:
    raise DeviceUpdateError(
      f"openpilot is on {current_branch!r}; switch it to {branch!r} first"
    )
  dirty = git_output(openpilot, "status", "--porcelain")
  if dirty:
    raise DeviceUpdateError(
      "openpilot has uncommitted changes; commit and push them before updating a device"
    )

  commit = git_output(openpilot, "rev-parse", "HEAD")
  remote_line = git_output(
    openpilot, "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}"
  )
  remote_commit = remote_line.split()[0] if remote_line else ""
  if remote_commit != commit:
    raise DeviceUpdateError(
      f"local {branch} commit {commit[:10]} is not pushed to origin ({remote_commit[:10] or 'missing'})"
    )
  return commit


def ssh_options(identity: Path | None) -> list[str]:
  options: list[str] = []
  if identity is not None:
    if not identity.is_file():
      raise DeviceUpdateError(f"SSH identity not found: {identity}")
    options += ["-i", str(identity)]
  for option in SSH_HOSTKEY_OPTIONS:
    options += ["-o", option]
  return options


def sync_helper(target: str, options: list[str]) -> None:
  helper = VAMOS_ROOT / "tools/device_update_remote.py"
  ssh_command = shlex.join(["ssh", *options])
  subprocess.run(
    [
      "rsync",
      "-az",
      "--chmod=F755",
      "-e",
      ssh_command,
      str(helper),
      f"{target}:{REMOTE_HELPER}",
    ],
    check=True,
  )


def remote_call(
  target: str,
  options: list[str],
  remote_root: str,
  command: str,
  branch: str | None = None,
) -> dict[str, object]:
  remote_command = ["python3", REMOTE_HELPER, "--root", remote_root, command]
  if branch is not None:
    remote_command += ["--branch", branch]
  output = command_output(["ssh", *options, target, shlex.join(remote_command)])
  try:
    result = json.loads(output)
  except json.JSONDecodeError as error:
    raise DeviceUpdateError(f"invalid response from device: {output}") from error
  if not isinstance(result, dict):
    raise DeviceUpdateError(f"invalid response from device: {output}")
  if result.get("error"):
    raise DeviceUpdateError(str(result["error"]))
  return result


def staged_commit_matches(status: dict[str, object], branch: str, commit: str) -> bool:
  return (
    status.get("staged_branch") == branch
    and status.get("staged_commit") == commit
    and status.get("staged_consistent") is True
    and status.get("update_available") is True
  )


def current_commit_matches(status: dict[str, object], branch: str, commit: str) -> bool:
  return (
    status.get("current_branch") == branch and status.get("current_commit") == commit
  )


def print_status(status: dict[str, object]) -> None:
  state = status.get("updater_state") or "unknown"
  current = str(status.get("current_commit") or "missing")[:10]
  staged = str(status.get("staged_commit") or "none")[:10]
  print(f"[device-update] state={state} current={current} staged={staged}", flush=True)


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
    description="Stage a pushed openpilot update on an SSH-connected device"
  )
  parser.add_argument("target", help="SSH target, for example comma@192.168.88.20")
  parser.add_argument("--branch", default="one")
  parser.add_argument("--openpilot", type=Path, default=VAMOS_ROOT.parent / "openpilot")
  parser.add_argument("--remote-root", default="/data/openpilot")
  parser.add_argument(
    "--identity",
    type=Path,
    default=Path(os.environ.get("DRAGON_SSH_KEY", "~/.ssh/comma_setup")).expanduser(),
  )
  parser.add_argument("--timeout", type=float, default=600.0)
  parser.add_argument("--poll-interval", type=float, default=2.0)
  parser.add_argument(
    "--install",
    action="store_true",
    help="request a reboot after the exact commit is finalized",
  )
  args = parser.parse_args(argv)

  try:
    target = normalize_target(args.target)
    openpilot = args.openpilot.expanduser().resolve()
    commit = validate_checkout(openpilot, args.branch)
    options = ssh_options(args.identity)
    print(
      f"[device-update] target={target} branch={args.branch} commit={commit[:10]}",
      flush=True,
    )

    sync_helper(target, options)
    status = remote_call(target, options, args.remote_root, "status")
    print_status(status)

    if current_commit_matches(status, args.branch, commit):
      print("[device-update] device is already running this commit")
      return 0

    if not staged_commit_matches(status, args.branch, commit):
      if status.get("updater_running") is not True:
        raise DeviceUpdateError(
          "openpilot updated is not running; bootstrap the device updater first"
        )
      failed_count_before = int(status.get("update_failed_count") or 0)
      remote_call(target, options, args.remote_root, "trigger", args.branch)

      deadline = time.monotonic() + args.timeout
      previous_summary = None
      while time.monotonic() < deadline:
        status = remote_call(target, options, args.remote_root, "status")
        summary = (
          status.get("updater_state"),
          status.get("staged_branch"),
          status.get("staged_commit"),
          status.get("update_available"),
        )
        if summary != previous_summary:
          print_status(status)
          previous_summary = summary
        if staged_commit_matches(status, args.branch, commit):
          break
        failed_count = int(status.get("update_failed_count") or 0)
        if status.get("updater_state") == "idle" and failed_count > failed_count_before:
          raise DeviceUpdateError(
            str(status.get("last_update_exception") or "device updater failed")
          )
        time.sleep(args.poll_interval)
      else:
        raise DeviceUpdateError(
          f"timed out after {args.timeout:g}s waiting for the finalized update"
        )

    print(f"[device-update] finalized {args.branch} at {commit[:10]}")
    if args.install:
      remote_call(target, options, args.remote_root, "install")
      print("[device-update] install requested; the device will reboot")
    else:
      print("[device-update] staged only; rerun with --install to reboot and apply it")
    return 0
  except (DeviceUpdateError, subprocess.CalledProcessError) as error:
    print(f"device-update: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
