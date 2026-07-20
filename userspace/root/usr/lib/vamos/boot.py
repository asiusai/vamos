#!/usr/bin/env python3
from __future__ import annotations

import argparse
import array
import contextlib
import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

from vamos.update import (
  HEALTHY_MARKER,
  STAGE1_MARKER,
  STATE_FILE,
  TRIAL_MARKER,
  WATCHDOG_DEVICE,
  WATCHDOG_DISARMED_MARKER,
  WATCHDOG_LOG,
  WATCHDOG_PID_FILE,
  WATCHDOG_READY_MARKER,
  UpdateError,
  cmdline,
  commit_boot,
  current_slot,
  load_state,
  rollback_boot,
  save_state,
)


WDIOC_SETTIMEOUT = (3 << 30) | (4 << 16) | (ord("W") << 8) | 6
WATCHDOG_TIMEOUT = 30
TRIAL_DEADLINE = 180
# The built-in QCOM driver can defer probing until its clock provider is ready.
# This wait only applies to a one-shot trial boot.
WATCHDOG_START_DEADLINE = 30
WATCHDOG_STOP_DEADLINE = 5
VERSION_FILE = Path("/VERSION")
PYTHON = "/usr/bin/python3"


def is_trial_boot() -> bool:
  return "vamos.trial=1" in cmdline().split()


def start_watchdog() -> None:
  if not is_trial_boot():
    return
  TRIAL_MARKER.touch()
  WATCHDOG_PID_FILE.unlink(missing_ok=True)
  WATCHDOG_READY_MARKER.unlink(missing_ok=True)
  WATCHDOG_DISARMED_MARKER.unlink(missing_ok=True)
  WATCHDOG_LOG.write_text(f"trial watchdog launcher starting with {PYTHON}\n")
  process = subprocess.Popen(
    [PYTHON, "/usr/bin/vamos-boot", "watchdog"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
    close_fds=True,
  )
  with WATCHDOG_LOG.open("a") as output:
    output.write(f"trial watchdog child {process.pid} launched\n")
  deadline = time.monotonic() + WATCHDOG_START_DEADLINE
  while time.monotonic() < deadline:
    if process.poll() is not None:
      raise UpdateError(f"trial watchdog exited during startup; see {WATCHDOG_LOG}")
    if WATCHDOG_READY_MARKER.exists():
      return
    time.sleep(0.05)
  with WATCHDOG_LOG.open("a") as output:
    output.write(f"trial watchdog child {process.pid} did not become ready\n")
    for detail in ("status", "wchan", "cmdline"):
      with contextlib.suppress(OSError):
        value = Path(f"/proc/{process.pid}/{detail}").read_text(errors="replace")
        output.write(f"/proc/{process.pid}/{detail}:\n{value}\n")
  process.terminate()
  with contextlib.suppress(subprocess.TimeoutExpired):
    process.wait(timeout=5)
  raise UpdateError(f"trial watchdog did not become ready; see {WATCHDOG_LOG}")


def watchdog() -> None:
  with WATCHDOG_LOG.open("a") as output:
    output.write("trial watchdog starting\n")
  try:
    deadline = time.monotonic() + TRIAL_DEADLINE
    open_deadline = time.monotonic() + WATCHDOG_START_DEADLINE
    while True:
      try:
        fd = os.open(WATCHDOG_DEVICE, os.O_WRONLY)
        break
      except OSError:
        if time.monotonic() >= open_deadline:
          raise
        time.sleep(0.05)
    timeout = array.array("i", [WATCHDOG_TIMEOUT])
    fcntl.ioctl(fd, WDIOC_SETTIMEOUT, timeout, True)
    WATCHDOG_PID_FILE.write_text(f"{os.getpid()}\n")
    WATCHDOG_READY_MARKER.touch()
    while True:
      if HEALTHY_MARKER.exists():
        os.write(fd, b"V")
        os.close(fd)
        fd = -1
        WATCHDOG_DISARMED_MARKER.touch()
        WATCHDOG_LOG.write_text("trial committed; watchdog disarmed\n")
        return
      if time.monotonic() >= deadline:
        with WATCHDOG_LOG.open("a") as output:
          output.write("trial health deadline expired; waiting for hardware reset\n")
        while True:
          time.sleep(WATCHDOG_TIMEOUT * 2)
      os.write(fd, b"\0")
      time.sleep(5)
  except Exception as exc:
    with WATCHDOG_LOG.open("a") as output:
      output.write(f"trial watchdog failed: {type(exc).__name__}: {exc}\n")
    raise
  finally:
    if "fd" in locals() and fd >= 0:
      with contextlib.suppress(OSError):
        os.close(fd)
    WATCHDOG_READY_MARKER.unlink(missing_ok=True)
    WATCHDOG_PID_FILE.unlink(missing_ok=True)


def reconcile() -> None:
  state = load_state()
  state_name = state.get("state")
  target = state.get("target_slot")
  if state_name not in ("ready", "booting") or target not in ("a", "b"):
    return
  active = current_slot()
  if is_trial_boot() and active == target:
    state["state"] = "booting"
    state["phase"] = "booting"
    state["boot_attempts"] = int(state.get("boot_attempts", 0)) + 1
    state["boot_started_at"] = int(time.time())
    save_state(state, "trial-booted")
    return

  if active != target:
    rollback_boot(active, target)
    state["state"] = "rolled_back"
    state["phase"] = "rolled_back"
    state["rolled_back_at"] = int(time.time())
    state["error"] = "trial slot did not commit before fallback boot"
    save_state(state, "rolled-back")


def commit() -> None:
  if not TRIAL_MARKER.exists():
    return
  if not WATCHDOG_READY_MARKER.exists():
    raise UpdateError("trial watchdog is not ready")
  if not STAGE1_MARKER.exists():
    raise UpdateError("runit stage 1 did not complete")
  if not os.path.ismount("/data"):
    raise UpdateError("persistent userdata is not mounted")

  state = load_state()
  active = current_slot()
  target = state.get("target_slot")
  previous = state.get("active_slot")
  if state.get("state") != "booting" or target != active or previous not in ("a", "b"):
    raise UpdateError("trial state does not match the running slot")

  version = str(state.get("version", ""))
  actual_version = VERSION_FILE.read_text().strip()
  if version not in ("", "unspecified") and version != actual_version:
    raise UpdateError(f"running OS version {actual_version!r} does not match update {version!r}")

  try:
    watchdog_pid = int(WATCHDOG_PID_FILE.read_text())
    os.kill(watchdog_pid, 0)
  except (FileNotFoundError, ValueError, ProcessLookupError) as exc:
    raise UpdateError("trial watchdog process is not running") from exc

  HEALTHY_MARKER.touch()
  deadline = time.monotonic() + WATCHDOG_STOP_DEADLINE
  while time.monotonic() < deadline and not WATCHDOG_DISARMED_MARKER.exists():
    time.sleep(0.05)
  if not WATCHDOG_DISARMED_MARKER.exists():
    HEALTHY_MARKER.unlink(missing_ok=True)
    raise UpdateError("trial watchdog did not disarm")

  try:
    current_entry, previous_entry = commit_boot(active, previous)
  except Exception:
    rollback_boot(previous, active)
    subprocess.run(["reboot", "-f"], check=False)
    raise
  state.update({
    "state": "committed",
    "phase": "committed",
    "progress": 100,
    "committed_at": int(time.time()),
    "previous_slot": previous,
    "active_slot": active,
    "active_entry": current_entry,
    "fallback_entry": previous_entry,
  })
  save_state(state, "committed")
  TRIAL_MARKER.unlink(missing_ok=True)
  os.sync()


def main(argv: Sequence[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description="vamOS trial-boot controller")
  parser.add_argument("command", choices=("early", "watchdog", "reconcile", "commit"))
  args = parser.parse_args(argv)
  try:
    if args.command == "early":
      start_watchdog()
    elif args.command == "watchdog":
      watchdog()
    elif args.command == "reconcile":
      reconcile()
    elif args.command == "commit":
      commit()
    return 0
  except Exception as exc:
    print(f"vamos-boot: ERROR: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
