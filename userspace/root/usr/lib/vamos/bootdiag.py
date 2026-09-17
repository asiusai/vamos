"""Boot progress on redundant ESPs, with bounded history on persistent userdata."""
from __future__ import annotations

import json
from pathlib import Path

from vamos import update


REPORT = Path("/run/vamos-boot.json")
HISTORY_DIR = Path("/data/vamos-boot")
BOOTSTATUS = Path("/run/vamos-watchdog-bootstatus")


def arguments() -> dict[str, str]:
  return dict(arg.split("=", 1) for arg in update.cmdline().split() if "=" in arg)


def record_stage(stage: str) -> None:
  if stage not in ("userspace", "healthy"):
    raise update.UpdateError(f"invalid userspace boot stage: {stage}")
  args = arguments()
  if "vamos.boot_count" not in args:
    return
  with update.update_lock():
    control = update.selected_boot_control()
    if control is None or control["boot_count"] != int(args["vamos.boot_count"]) or control["boot_slot"] != update.current_slot():
      raise update.UpdateError("boot diagnostics do not match this boot")
    if control["boot_stage"] != stage:
      control["boot_stage"] = stage
      control["generation"] = int(control["generation"]) + 1
      payload = update.encode_boot_control(control)
      written = [update._write_control_to_slot(slot, payload) for slot in ("b", "a")]
      if not any(written):
        raise update.UpdateError("no ESP accepted boot diagnostics")


def report(stage: str) -> dict:
  args = arguments()
  try:
    bootstatus = int(BOOTSTATUS.read_text())
  except (OSError, ValueError):
    bootstatus = None
  previous_stage = args.get("vamos.prev_stage", "unknown")
  pon = args.get("vamos.pon", "unavailable")
  result = {
    "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    "boot_count": int(args.get("vamos.boot_count", "0")),
    "slot": update.current_slot(),
    "trial": args.get("vamos.trial") == "1",
    "stage": stage,
    "previous": {
      "boot_count": int(args.get("vamos.prev_count", "0")),
      "slot": args.get("vamos.prev_slot", "-"),
      "stage": previous_stage,
      "incomplete": previous_stage not in ("unknown", "healthy"),
    },
    "reset": {
      "watchdog_bootstatus": bootstatus,
      "watchdog_cardreset": bool(bootstatus & 0x20) if bootstatus is not None else None,
      "xbl_pon_status_hex": None if pon == "unavailable" else pon,
    },
  }
  payload = (json.dumps(result, indent=2) + "\n").encode()
  update._atomic_write_bytes(REPORT, payload)
  if update.os.path.ismount("/data"):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    update._atomic_write_bytes(HISTORY_DIR / "latest.json", payload)
    if result["previous"]["incomplete"]:
      update._atomic_write_bytes(HISTORY_DIR / "last-incomplete.json", payload)
    history_path = HISTORY_DIR / "history.json"
    try:
      history = json.loads(history_path.read_text())
      if not isinstance(history, list):
        history = []
    except (OSError, ValueError):
      history = []
    history = [entry for entry in history if isinstance(entry, dict) and entry.get("boot_id") != result["boot_id"]]
    history.append(result)
    update._atomic_write_bytes(history_path, (json.dumps(history[-32:], indent=2) + "\n").encode())
  return result


def diagnose(stage: str) -> None:
  record_stage(stage)
  report(stage)


def status() -> dict:
  if REPORT.exists():
    return json.loads(REPORT.read_text())
  return report("unknown")
