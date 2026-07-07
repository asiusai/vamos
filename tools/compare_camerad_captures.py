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

ROAD_THRESHOLDS = {
  "min_y_median": 70.0,
  "max_y_median": 190.0,
  "min_y_range": 50.0,
  "max_y_clip_lo": 0.20,
  "max_y_clip_hi": 0.20,
  "max_luma_clip_hi": 0.08,
  "max_rgb_spread": 12.0,
  "max_uv_median_offset": 2.0,
  "max_uv_center_median_offset": 1.5,
  "min_mean_chroma": 6.0,
  "min_uv_abs": 4.0,
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


def fail_outside(value: float, low: float, high: float) -> bool:
  return value < low or value > high


def road_failures(stats: dict) -> list[str]:
  y_range = float(stats["y_p99"]) - float(stats["y_p01"])
  u_median = float(stats.get("u_median", 999.0))
  v_median = float(stats.get("v_median", 999.0))
  u_center = float(stats.get("u_center_median", 999.0))
  v_center = float(stats.get("v_center_median", 999.0))
  uv_med_off = max(abs(u_median - 128.0), abs(v_median - 128.0))
  uv_center_off = max(abs(u_center - 128.0), abs(v_center - 128.0))

  checks = [
    ("y", float(stats["y_median"]), ROAD_THRESHOLDS["min_y_median"], ROAD_THRESHOLDS["max_y_median"]),
    ("range", y_range, ROAD_THRESHOLDS["min_y_range"], 999.0),
    ("clip_lo", float(stats["y_clip_lo_frac"]), 0.0, ROAD_THRESHOLDS["max_y_clip_lo"]),
    ("clip_hi", float(stats["y_clip_hi_frac"]), 0.0, ROAD_THRESHOLDS["max_y_clip_hi"]),
    ("luma_clip", float(stats["luma_clip_hi_frac"]), 0.0, ROAD_THRESHOLDS["max_luma_clip_hi"]),
    ("rgb", float(stats["rgb_median_spread"]), 0.0, ROAD_THRESHOLDS["max_rgb_spread"]),
    ("uv_med", uv_med_off, 0.0, ROAD_THRESHOLDS["max_uv_median_offset"]),
    ("uvc_med", uv_center_off, 0.0, ROAD_THRESHOLDS["max_uv_center_median_offset"]),
    ("chroma", float(stats["mean_chroma"]), ROAD_THRESHOLDS["min_mean_chroma"], 999.0),
    ("uv_abs", float(stats["uv_abs_mean"]), ROAD_THRESHOLDS["min_uv_abs"], 999.0),
  ]
  return [name for name, value, low, high in checks if fail_outside(value, low, high)]


def quality_score(stats: dict) -> float:
  y_med = float(stats["y_median"])
  y_range = float(stats["y_p99"]) - float(stats["y_p01"])
  clip_hi = float(stats["luma_clip_hi_frac"])
  rgb_spread = float(stats["rgb_median_spread"])
  uv_abs = float(stats["uv_abs_mean"])
  mean_chroma = float(stats["mean_chroma"])
  u_median = float(stats.get("u_median", 999.0))
  v_median = float(stats.get("v_median", 999.0))
  u_center = float(stats.get("u_center_median", 999.0))
  v_center = float(stats.get("v_center_median", 999.0))
  uv_med_off = max(abs(u_median - 128.0), abs(v_median - 128.0))
  uv_center_off = max(abs(u_center - 128.0), abs(v_center - 128.0))

  # Bring-up heuristic, not an image-quality model. Lower means closer to the
  # current road-profile thresholds while preferring moderate exposure.
  score = 0.0
  score += 35.0 * max(0.0, clip_hi / ROAD_THRESHOLDS["max_luma_clip_hi"] - 1.0)
  score += 12.0 * max(0.0, rgb_spread / ROAD_THRESHOLDS["max_rgb_spread"] - 1.0)
  score += 24.0 * max(0.0, uv_med_off / ROAD_THRESHOLDS["max_uv_median_offset"] - 1.0)
  score += 24.0 * max(0.0, uv_center_off / ROAD_THRESHOLDS["max_uv_center_median_offset"] - 1.0)
  score += 8.0 * max(0.0, ROAD_THRESHOLDS["min_y_range"] / max(y_range, 1.0) - 1.0)
  score += 8.0 * max(0.0, ROAD_THRESHOLDS["min_mean_chroma"] - mean_chroma)
  score += 8.0 * max(0.0, ROAD_THRESHOLDS["min_uv_abs"] - uv_abs)
  score += abs(y_med - 120.0) / 12.0
  score += clip_hi * 8.0
  score += rgb_spread / 24.0
  score += uv_center_off / 3.0
  return score


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("runs", nargs="+", help="run dir, or label=/path/to/run")
  parser.add_argument("--cam", choices=("cam2", "cam3", "both"), default="both")
  parser.add_argument("--rank", action="store_true", help="print per-run score summary sorted best-first")
  args = parser.parse_args()

  cams = ("cam2", "cam3") if args.cam == "both" else (args.cam,)
  rows = []
  runs: dict[str, dict[str, float | int]] = {}
  for run_arg in args.runs:
    label, run_dir = label_for(run_arg)
    for cam in cams:
      stats = load_stats(run_dir, cam)
      failures = road_failures(stats)
      score = quality_score(stats)
      run = runs.setdefault(label, {"score": 0.0, "failures": 0, "cams": 0})
      run["score"] = float(run["score"]) + score
      run["failures"] = int(run["failures"]) + len(failures)
      run["cams"] = int(run["cams"]) + 1
      u_median = float(stats.get("u_median", 999.0))
      v_median = float(stats.get("v_median", 999.0))
      u_center = float(stats.get("u_center_median", 999.0))
      v_center = float(stats.get("v_center_median", 999.0))
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
        "uv_med_off": max(abs(u_median - 128.0), abs(v_median - 128.0)),
        "uvc_med_off": max(abs(u_center - 128.0), abs(v_center - 128.0)),
        "mean_chroma": stats["mean_chroma"],
        "clip_hi": stats["luma_clip_hi_frac"],
        "road_fails": ",".join(failures) if failures else "pass",
        "score": score,
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
    ("uv_med_off", "uvm_off"),
    ("uvc_med_off", "uvc_off"),
    ("mean_chroma", "chroma"),
    ("clip_hi", "clip_hi"),
    ("road_fails", "road"),
    ("score", "score"),
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

  if args.rank:
    print()
    print("run_score  road_fails  run")
    print("---------  ----------  ---")
    for label, data in sorted(runs.items(), key=lambda item: (int(item[1]["failures"]), float(item[1]["score"]))):
      cams_count = max(1, int(data["cams"]))
      print(f"{float(data['score']) / cams_count:9.2f}  {int(data['failures']):10d}  {label}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
