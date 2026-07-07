#!/usr/bin/env python3
"""Run the CAM2/CAM3 hardware VFE acceptance gates and write one summary."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path


SUMMARY_FILE = "vfe-acceptance-summary.json"


def default_out_dir() -> Path:
  stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
  return Path(f"/tmp/dragon_os04_bench/vfe-acceptance-{stamp}")


def run_cmd(cmd: list[str], dry_run: bool) -> int:
  print("+", " ".join(cmd), flush=True)
  if dry_run:
    return 0
  return subprocess.run(cmd, check=False).returncode


def load_json(path: Path) -> dict | None:
  if not path.exists():
    return None
  try:
    with path.open() as f:
      return json.load(f)
  except (OSError, json.JSONDecodeError):
    return None


def camera_extract(snapshot: dict | None) -> dict:
  if not isinstance(snapshot, dict):
    return {}
  ret = {}
  for cam in ("cam2", "cam3"):
    cam_data = snapshot.get("cameras", {}).get(cam, {})
    image = cam_data.get("image", {})
    ret[cam] = {
      "vfe_pix_v4l2": cam_data.get("vfe_pix_v4l2"),
      "dmabuf_nv12": cam_data.get("dmabuf_nv12"),
      "debug_frames": cam_data.get("debug_frames"),
      "median_fps": cam_data.get("median_fps"),
      "slow_gaps": cam_data.get("slow_gaps"),
      "y_median": image.get("y_median"),
      "rgb_median_spread": image.get("rgb_median_spread"),
      "max_uv_center_median_offset": image.get("max_uv_center_median_offset"),
      "uv_hf_abs_mean": image.get("uv_hf_abs_mean"),
      "mean_chroma": image.get("mean_chroma"),
    }
  return ret


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--openpilot-dir", default="/data/openpilot_hw_vfe")
  parser.add_argument("--out-dir", type=Path, default=None)
  parser.add_argument("--snapshot-settle", type=float, default=7.0)
  parser.add_argument("--snapshot-profile", default="daylight-road", choices=("bench", "road", "road-spatial", "daylight-road"))
  parser.add_argument("--modeld-duration", type=float, default=25.0)
  parser.add_argument("--modeld-settle", type=float, default=7.0)
  parser.add_argument("--target-grey", type=float, default=0.0, help="OS04 AE target grey fraction; 0 uses camerad defaults")
  parser.add_argument("--pull-timeout", type=float, default=60.0)
  parser.add_argument("--env", action="append", default=[], metavar="NAME=VALUE")
  parser.add_argument("--skip-snapshot", action="store_true")
  parser.add_argument("--skip-modeld", action="store_true")
  parser.add_argument("--dry-run", action="store_true")
  args = parser.parse_args()

  if args.target_grey < 0.0:
    parser.error("--target-grey must be non-negative")
  if args.pull_timeout <= 0.0:
    parser.error("--pull-timeout must be positive")

  out_dir = args.out_dir or default_out_dir()
  snapshot_dir = out_dir / "snapshot"
  modeld_dir = out_dir / "modeld"
  out_dir.mkdir(parents=True, exist_ok=True)

  here = Path(__file__).resolve().parent
  summary: dict = {
    "out_dir": str(out_dir),
    "openpilot_dir": args.openpilot_dir,
    "snapshot": {
      "skipped": bool(args.skip_snapshot),
      "out_dir": str(snapshot_dir),
      "profile": args.snapshot_profile,
    },
    "modeld": {
      "skipped": bool(args.skip_modeld),
      "out_dir": str(modeld_dir),
    },
    "visual_check_required": True,
    "host_montage": "/tmp/asius-cams-latest.jpg",
  }

  common_env_args: list[str] = []
  for env in args.env:
    common_env_args.extend(["--env", env])

  snapshot_rc = 0
  if not args.skip_snapshot:
    snapshot_cmd = [
      sys.executable,
      str(here / "camerad_capture_latest.py"),
      "--openpilot-dir", args.openpilot_dir,
      "--cam", "both",
      "--out-dir", str(snapshot_dir),
      "--settle", str(args.snapshot_settle),
      "--target-grey", str(args.target_grey),
      "--validate-vfe",
      "--validate-quality-profile", args.snapshot_profile,
      "--check-dmesg",
      "--log-awb",
      "--log-ae",
      "--pull-timeout", str(args.pull_timeout),
      *common_env_args,
    ]
    summary["snapshot"]["command"] = snapshot_cmd
    snapshot_rc = run_cmd(snapshot_cmd, args.dry_run)
    summary["snapshot"]["returncode"] = snapshot_rc
    snapshot_json = load_json(snapshot_dir / "latest-camerad-vfe-summary.json")
    summary["snapshot"]["summary_json"] = str(snapshot_dir / "latest-camerad-vfe-summary.json")
    summary["snapshot"]["passed"] = bool(snapshot_json.get("passed", False)) if snapshot_json else False
    summary["snapshot"]["hardware_path_passed"] = bool(snapshot_json.get("hardware_path_passed", False)) if snapshot_json else False
    summary["snapshot"]["image_quality_passed"] = bool(snapshot_json.get("image_quality_passed", False)) if snapshot_json else False
    summary["snapshot"]["failures"] = snapshot_json.get("failures", []) if snapshot_json else ["missing snapshot summary"]
    summary["snapshot"]["cameras"] = camera_extract(snapshot_json)

  modeld_rc = 0
  if not args.skip_modeld:
    modeld_cmd = [
      sys.executable,
      str(here / "validate_live_vfe_modeld.py"),
      "--openpilot-dir", args.openpilot_dir,
      "--out-dir", str(modeld_dir),
      "--duration", str(args.modeld_duration),
      "--settle", str(args.modeld_settle),
      "--target-grey", str(args.target_grey),
      "--check-dmesg",
      "--pull-timeout", str(args.pull_timeout),
      *common_env_args,
    ]
    summary["modeld"]["command"] = modeld_cmd
    modeld_rc = run_cmd(modeld_cmd, args.dry_run)
    summary["modeld"]["returncode"] = modeld_rc
    modeld_json = load_json(modeld_dir / "live-vfe-modeld-summary.json")
    summary["modeld"]["summary_json"] = str(modeld_dir / "live-vfe-modeld-summary.json")
    summary["modeld"]["passed"] = modeld_rc == 0
    if modeld_json:
      summary["modeld"]["road_frames"] = modeld_json.get("roadCameraState", {}).get("frames")
      summary["modeld"]["wide_frames"] = modeld_json.get("wideRoadCameraState", {}).get("frames")
      summary["modeld"]["model_frames"] = modeld_json.get("modelV2", {}).get("frames")
      summary["modeld"]["model_valid"] = modeld_json.get("modelV2", {}).get("valid")
      summary["modeld"]["steady_state_max_execution_ms"] = modeld_json.get("modelV2", {}).get("steady_state_max_execution_ms")
      summary["modeld"]["steady_state_max_frame_drop_pct"] = modeld_json.get("modelV2", {}).get("steady_state_max_frame_drop_pct")
      summary["modeld"]["dmesg_forbidden_matches"] = len(modeld_json.get("dmesg", {}).get("forbidden_matches", []))
    else:
      summary["modeld"]["failures"] = ["missing modeld summary"]

  snapshot_ok = args.skip_snapshot or snapshot_rc == 0
  modeld_ok = args.skip_modeld or modeld_rc == 0
  summary["passed"] = snapshot_ok and modeld_ok
  summary["note"] = (
    "A passing machine summary still needs a human visual check of "
    "/tmp/asius-cams-latest.jpg for the final daylight-road acceptance."
  )

  summary_path = out_dir / SUMMARY_FILE
  summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
  print(f"summary_json: {summary_path}")
  print(f"summary: passed={summary['passed']} snapshot_rc={snapshot_rc} modeld_rc={modeld_rc}")
  return 0 if summary["passed"] else 1


if __name__ == "__main__":
  raise SystemExit(main())
