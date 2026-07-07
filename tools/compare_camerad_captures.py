#!/usr/bin/env python3
"""Compare CAM2/CAM3 raw stats from camerad_capture_latest.py output dirs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


STAT_FILES = {
  "cam2": "latest-camerad-road-raw-stats.json",
  "cam3": "latest-camerad-wide-raw-stats.json",
}


def fmt(value: object, precision: int = 2) -> str:
  if isinstance(value, float):
    return f"{value:.{precision}f}"
  return str(value)


def load_stats(run_dir: Path, cam: str) -> dict:
  path = run_dir / STAT_FILES[cam]
  with path.open() as f:
    return json.load(f)


def label_for(arg: str) -> tuple[str, Path]:
  if "=" in arg:
    label, path = arg.split("=", 1)
    return label, Path(path)
  path = Path(arg)
  return path.name, path


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("runs", nargs="+", help="run dir, or label=/path/to/run")
  parser.add_argument("--cam", choices=("cam2", "cam3", "both"), default="both")
  args = parser.parse_args()

  cams = ("cam2", "cam3") if args.cam == "both" else (args.cam,)
  rows = []
  for run_arg in args.runs:
    label, run_dir = label_for(run_arg)
    for cam in cams:
      stats = load_stats(run_dir, cam)
      rows.append({
        "run": label,
        "cam": cam,
        "rgb_med": ",".join(fmt(x, 0) for x in stats["rgb_median"]),
        "rgb_spread": stats["rgb_median_spread"],
        "y_med": stats["y_median"],
        "y_center": stats["y_center_median"],
        "y_p01": stats["y_p01"],
        "y_p99": stats["y_p99"],
        "uv_abs": stats["uv_abs_mean"],
        "uv_center": stats["uv_center_abs_mean"],
        "mean_chroma": stats["mean_chroma"],
        "clip_hi": stats["luma_clip_hi_frac"],
      })

  columns = [
    ("run", "run"),
    ("cam", "cam"),
    ("rgb_med", "rgb_med"),
    ("rgb_spread", "spread"),
    ("y_med", "y"),
    ("y_center", "yc"),
    ("y_p01", "p01"),
    ("y_p99", "p99"),
    ("uv_abs", "uv"),
    ("uv_center", "uvc"),
    ("mean_chroma", "chroma"),
    ("clip_hi", "clip_hi"),
  ]
  rendered = []
  for row in rows:
    rendered.append({
      key: fmt(row[key], 3 if key == "clip_hi" else 2)
      for key, _ in columns
    })

  widths = {}
  for key, title in columns:
    widths[key] = max(len(title), *(len(row[key]) for row in rendered))

  header = "  ".join(title.ljust(widths[key]) for key, title in columns)
  print(header)
  print("  ".join("-" * widths[key] for key, _ in columns))
  for row in rendered:
    print("  ".join(row[key].ljust(widths[key]) for key, _ in columns))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
