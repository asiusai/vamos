#!/usr/bin/env python3
"""Print the next hardware action from an OS04 raw-bench summary JSON."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path


STOP_CONTROL = {
  "CONTROL_FAIL": "Stop: Dragon CSID/RDI testgen failed. Fix receiver/software state before trusting the camera result.",
  "INIT_MISMATCH": "Stop: OS04 register init/readback mismatched. Fix I2C/init before doing more physical MIPI tests.",
  "SENSOR_START_FAIL": "Stop: OS04 sensor_start failed. Fix power/I2C/reset before doing more physical MIPI tests.",
  "UNKNOWN": "Stop: verdict is UNKNOWN. Inspect the full log before changing hardware.",
}


def latest_summary(out_dir: Path, label: str | None) -> Path:
  pattern = "*.summary.json" if not label else f"*{label}*.summary.json"
  matches = sorted(out_dir.glob(pattern), key=lambda path: path.stat().st_mtime)
  if not matches:
    label_text = "" if not label else f" matching label {label!r}"
    raise FileNotFoundError(f"no summary JSON files{label_text} in {out_dir}")
  return matches[-1]


def load_summary(path: Path) -> dict:
  with path.open() as fp:
    return json.load(fp)


def camera_verdicts(summary: dict) -> dict[str, str]:
  cameras = summary.get("cameras", {})
  return {
    cam: str(result.get("verdict", "UNKNOWN"))
    for cam, result in sorted(cameras.items())
  }


def stage_action(stage: str, verdict: str) -> str:
  if verdict in STOP_CONTROL:
    return STOP_CONTROL[verdict]

  if stage == "pvdd":
    if verdict == "FRAMES":
      return (
        "PVDD is a root cause. Keep the rework result, then change the next PCB: "
        "+2V8_AVDD -> R_PVDD_LINK/FB_PVDD -> OS04_PVDD, with C18 local and a measurable pad."
      )
    if verdict == "CSID_ACTIVITY_NO_FRAMES":
      return (
        "PVDD changed CSI receiver behavior but did not produce frames. Keep PVDD powered if current/heat are normal, "
        "then run the all-active-pair P/N swap interposer."
      )
    if verdict in ("NO_CSI_ACTIVITY", "ZERO_FRAMES_NO_CSID_READS"):
      return (
        "PVDD alone did not fix it. If PVDD current/heat are normal, keep PVDD powered for the P/N swap test; "
        "if not, remove the jumper and require OS04 application-schematic proof."
      )

  if stage == "pn-all":
    if verdict == "FRAMES":
      return (
        "All-active P/N swap produced frames. The physical polarity is wrong; copy that swap into the next PCB "
        "or implement real CSIPHY polarity programming."
      )
    if verdict == "CSID_ACTIVITY_NO_FRAMES":
      return (
        "P/N swap changed receiver state but still no frames. Test split combinations next: pn-clock-only, "
        "pn-clock-d0, and pn-clock-d1."
      )
    if verdict in ("NO_CSI_ACTIVITY", "ZERO_FRAMES_NO_CSID_READS"):
      return (
        "All-active P/N swap did not help. Stop spending time on polarity alone; prioritize EVDD selectable "
        "next-spin, known-good OS04 module control, MIPI clock scope proof, or OS04 ball-map/application proof."
      )

  if stage == "pn-split":
    if verdict == "FRAMES":
      return "This split P/N population produced frames. Copy this exact polarity population into the next PCB variant."
    if verdict == "CSID_ACTIVITY_NO_FRAMES":
      return "This split population affects CSI but is incomplete. Compare against the other split combinations."
    if verdict in ("NO_CSI_ACTIVITY", "ZERO_FRAMES_NO_CSID_READS"):
      return "This split population did not help. Try the next split combo, or stop P/N testing if all combos match this."

  if stage == "baseline":
    if verdict == "FRAMES":
      return "Baseline produced frames. Preserve this hardware state and pull the raw frame/log for inspection."
    if verdict in ("NO_CSI_ACTIVITY", "ZERO_FRAMES_NO_CSID_READS"):
      return "Baseline still has no CSI activity. Continue with PVDD first, then P/N only if PVDD current/heat are normal."
    if verdict == "CSID_ACTIVITY_NO_FRAMES":
      return "Baseline has CSI activity but no frames. Prioritize polarity/lane format and packet decode checks."

  return f"No stage rule for stage={stage!r} verdict={verdict!r}; inspect the full log."


def print_actions(summary_path: Path, stage: str) -> int:
  summary = load_summary(summary_path)
  verdicts = camera_verdicts(summary)
  if not verdicts:
    print(f"summary={summary_path}")
    print("No cameras found in summary JSON.")
    return 1

  print(f"summary={summary_path}")
  print(f"stage={stage}")
  exit_code = 0
  for cam, verdict in verdicts.items():
    print(f"{cam}: verdict={verdict}")
    print(f"{cam}: next={stage_action(stage, verdict)}")
    if verdict in STOP_CONTROL or verdict == "UNKNOWN":
      exit_code = 2
  return exit_code


def self_test() -> int:
  samples = [
    ("pvdd", "FRAMES", "PVDD is a root cause"),
    ("pvdd", "NO_CSI_ACTIVITY", "PVDD alone did not fix"),
    ("pn-all", "FRAMES", "physical polarity is wrong"),
    ("pn-all", "CSID_ACTIVITY_NO_FRAMES", "Test split combinations"),
    ("pn-all", "NO_CSI_ACTIVITY", "Stop spending time on polarity alone"),
    ("pn-split", "FRAMES", "Copy this exact polarity"),
  ]
  with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    for idx, (stage, verdict, expected) in enumerate(samples):
      summary_path = tmp_path / f"{idx}.summary.json"
      summary_path.write_text(json.dumps({
        "cameras": {
          "cam3": {
            "verdict": verdict,
          },
        },
      }))
      action = stage_action(stage, verdict)
      if expected not in action:
        print(f"self-test failed: stage={stage} verdict={verdict}", file=sys.stderr)
        print(f"expected substring={expected!r}", file=sys.stderr)
        print(f"action={action}", file=sys.stderr)
        return 1
  print("PASS os04_next_action self-test")
  return 0


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("summary_json", nargs="?", help="path to *.summary.json")
  parser.add_argument("--stage", required=False, default="pvdd",
                      choices=("baseline", "pvdd", "pn-all", "pn-split"),
                      help="hardware test stage represented by the summary")
  parser.add_argument("--latest-label", help="use newest /tmp summary whose filename contains this label")
  parser.add_argument("--out-dir", default="/tmp/dragon_os04_bench",
                      help="directory searched by --latest-label")
  parser.add_argument("--self-test", action="store_true")
  args = parser.parse_args()

  if args.self_test:
    return self_test()

  if args.summary_json:
    summary_path = Path(args.summary_json)
  else:
    summary_path = latest_summary(Path(args.out_dir), args.latest_label)

  return print_actions(summary_path, args.stage)


if __name__ == "__main__":
  raise SystemExit(main())
