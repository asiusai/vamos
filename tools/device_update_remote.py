#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path


PARAMS_DIR = Path("/data/params/d")
STAGING_ROOT = Path("/data/safe_staging")


def git_value(root: Path, *args: str) -> str | None:
  result = subprocess.run(
    ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
  )
  return result.stdout.strip() if result.returncode == 0 else None


def read_param(name: str) -> str | None:
  try:
    return (PARAMS_DIR / name).read_text().strip()
  except (FileNotFoundError, PermissionError, OSError):
    return None


def updater_pids() -> list[int]:
  result = subprocess.run(
    ["pgrep", "-f", "openpilot.system.updated.updated"],
    capture_output=True,
    text=True,
    check=False,
  )
  if result.returncode not in (0, 1):
    raise RuntimeError(f"pgrep failed: {result.stderr.strip()}")
  return [int(pid) for pid in result.stdout.split() if pid.isdigit()]


def load_params(root: Path):
  sys.path.insert(0, str(root))
  from openpilot.common.params import Params

  return Params()


def status(root: Path) -> dict[str, object]:
  finalized = STAGING_ROOT / "finalized"
  return {
    "current_branch": git_value(root, "branch", "--show-current"),
    "current_commit": git_value(root, "rev-parse", "HEAD"),
    "staged_branch": git_value(finalized, "branch", "--show-current"),
    "staged_commit": git_value(finalized, "rev-parse", "HEAD"),
    "staged_consistent": (finalized / ".overlay_consistent").is_file(),
    "update_available": read_param("UpdateAvailable") == "1",
    "updater_state": read_param("UpdaterState"),
    "updater_target_branch": read_param("UpdaterTargetBranch"),
    "update_failed_count": int(read_param("UpdateFailedCount") or 0),
    "last_update_exception": read_param("LastUpdateException"),
    "updater_running": bool(updater_pids()),
  }


def trigger(root: Path, branch: str) -> dict[str, object]:
  pids = updater_pids()
  if not pids:
    raise RuntimeError("openpilot updated is not running on the device")
  load_params(root).put("UpdaterTargetBranch", branch, block=True)
  for pid in pids:
    os.kill(pid, signal.SIGHUP)
  return {"triggered": True, "branch": branch, "pids": pids}


def install(root: Path) -> dict[str, object]:
  current = status(root)
  if not current["update_available"] or not current["staged_consistent"]:
    raise RuntimeError("no finalized update is ready to install")
  load_params(root).put_bool("DoReboot", True, block=True)
  return {"install_requested": True}


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--root", default="/data/openpilot")
  parser.add_argument("command", choices=("status", "trigger", "install"))
  parser.add_argument("--branch")
  args = parser.parse_args()
  root = Path(args.root)

  try:
    if args.command == "status":
      result = status(root)
    elif args.command == "trigger":
      if not args.branch:
        raise RuntimeError("--branch is required for trigger")
      result = trigger(root, args.branch)
    else:
      result = install(root)
    print(json.dumps(result, sort_keys=True))
    return 0
  except Exception as error:
    print(json.dumps({"error": str(error)}, sort_keys=True))
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
