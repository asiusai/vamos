#!/usr/bin/env python3
"""Show the current OS04 bench state and the next raw-only bench command."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


DEFAULT_OUT_DIR = Path("/tmp/dragon_os04_bench")


def stage_from_label(path: Path) -> str:
  name = path.name
  if "pvdd-rework" in name:
    return "pvdd"
  if "pn-all-active-swap" in name:
    return "pn-all"
  if "pn-clock" in name:
    return "pn-split"
  if "pn-straight-control" in name or "known-good-os04" in name:
    return "baseline"
  return "baseline"


def list_summaries(out_dir: Path) -> list[Path]:
  if not out_dir.exists():
    return []
  return sorted(out_dir.glob("*.summary.json"), key=lambda path: path.stat().st_mtime)


def load_summary(path: Path) -> dict:
  with path.open() as fp:
    return json.load(fp)


def camera_verdicts(summary: dict) -> dict[str, str]:
  cameras = summary.get("cameras", {})
  return {
    cam: str(result.get("verdict", "UNKNOWN"))
    for cam, result in sorted(cameras.items())
  }


def next_action_for(stage: str, verdict: str) -> str:
  if verdict in ("CONTROL_FAIL", "INIT_MISMATCH", "SENSOR_START_FAIL", "UNKNOWN"):
    return "Inspect the full log and fix this control failure before changing hardware."

  if stage == "pvdd":
    if verdict == "FRAMES":
      return "PVDD is proven. Update next PCB with OS04_PVDD fed from +2V8 through a configurable link."
    if verdict == "CSID_ACTIVITY_NO_FRAMES":
      return "Keep PVDD powered if safe and run the all-active P/N swap."
    if verdict in ("NO_CSI_ACTIVITY", "ZERO_FRAMES_NO_CSID_READS"):
      return "PVDD alone did not fix it. If current/heat are normal, keep PVDD powered and run all-active P/N swap."

  if stage == "pn-all":
    if verdict == "FRAMES":
      return "P/N polarity is proven wrong. Copy the swap into the next PCB or implement real CSIPHY polarity."
    if verdict == "CSID_ACTIVITY_NO_FRAMES":
      return "Run split P/N tests: pn-clock-only, pn-clock-d0, pn-clock-d1."
    if verdict in ("NO_CSI_ACTIVITY", "ZERO_FRAMES_NO_CSID_READS"):
      return "Stop polarity-only testing. Prioritize EVDD selectable next-spin, known-good OS04 module, or scope proof."

  if stage == "pn-split":
    if verdict == "FRAMES":
      return "This split polarity population is the one to copy into the next PCB."
    if verdict == "CSID_ACTIVITY_NO_FRAMES":
      return "Compare this against the other split P/N combinations."
    return "Try the next split P/N combination, unless all split tests have already failed."

  if stage == "baseline":
    if verdict == "FRAMES":
      return "Baseline streams. Preserve this exact hardware/software state and archive the raw log."
    if verdict == "CSID_ACTIVITY_NO_FRAMES":
      return "Receiver sees something. Prioritize polarity/lane/format decode checks."
    if verdict in ("NO_CSI_ACTIVITY", "ZERO_FRAMES_NO_CSID_READS"):
      return "Run PVDD rework first."

  return "No rule matched. Inspect the full log."


def print_next_commands() -> None:
  print("next_command_pvdd:")
  print("  cd /home/john/asius/vamos")
  print("  tools/os04_raw_bench_log.py --label pvdd-rework -- --cam cam3 --rebuild")
  print("  tools/os04_next_action.py --stage pvdd --latest-label pvdd-rework")
  print("next_command_pn_if_needed:")
  print("  cd /home/john/asius/vamos")
  print("  tools/os04_raw_bench_log.py --label pn-all-active-swap -- --cam cam3 --rebuild")
  print("  tools/os04_next_action.py --stage pn-all --latest-label pn-all-active-swap")


def print_status(out_dir: Path, limit: int) -> int:
  print("active_state=/home/john/asius/hardware/cam-v2/active-debug-state.md")
  print("current_boundary=before Dragon CSID sees valid external CSI packets")
  print("raw_path=no camerad, no Spectra/CamX, no openpilot camera stack")
  print(f"summary_dir={out_dir}")

  summaries = list_summaries(out_dir)
  if not summaries:
    print("summaries=none")
    print("bench_state=waiting_for_pvdd_rework_result")
    print_next_commands()
    return 0

  print(f"summaries={len(summaries)}")
  for path in summaries[-limit:]:
    stage = stage_from_label(path)
    summary = load_summary(path)
    verdicts = camera_verdicts(summary)
    verdict_text = ", ".join(f"{cam}:{verdict}" for cam, verdict in verdicts.items()) or "no-cameras"
    print(f"summary={path} stage={stage} verdicts={verdict_text}")
    for cam, verdict in verdicts.items():
      print(f"next[{cam}]={next_action_for(stage, verdict)}")
  return 0


def self_test() -> int:
  with tempfile.TemporaryDirectory() as tmp:
    out_dir = Path(tmp)
    (out_dir / "20260705T000000Z-pvdd-rework.summary.json").write_text(json.dumps({
      "cameras": {"cam3": {"verdict": "NO_CSI_ACTIVITY"}},
    }))
    (out_dir / "20260705T000001Z-pn-all-active-swap.summary.json").write_text(json.dumps({
      "cameras": {"cam3": {"verdict": "CSID_ACTIVITY_NO_FRAMES"}},
    }))
    summaries = list_summaries(out_dir)
    if len(summaries) != 2:
      print("self-test failed: summary count", file=sys.stderr)
      return 1
    stages_by_name = {path.name: stage_from_label(path) for path in summaries}
    checks = [
      stages_by_name["20260705T000000Z-pvdd-rework.summary.json"] == "pvdd",
      stages_by_name["20260705T000001Z-pn-all-active-swap.summary.json"] == "pn-all",
      "Run split P/N tests" in next_action_for("pn-all", "CSID_ACTIVITY_NO_FRAMES"),
      "Run PVDD rework first" in next_action_for("baseline", "NO_CSI_ACTIVITY"),
    ]
    if not all(checks):
      print("self-test failed: rule mismatch", file=sys.stderr)
      return 1
  print("PASS os04_bench_status self-test")
  return 0


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
  parser.add_argument("--limit", type=int, default=5)
  parser.add_argument("--self-test", action="store_true")
  args = parser.parse_args()

  if args.self_test:
    return self_test()
  return print_status(Path(args.out_dir), args.limit)


if __name__ == "__main__":
  raise SystemExit(main())
