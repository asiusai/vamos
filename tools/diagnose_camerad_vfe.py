#!/usr/bin/env python3
"""Summarize VFE capture health and scene-dependent image issues."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


SUMMARY_FILE = "latest-camerad-vfe-summary.json"


def camera_list(selection: str) -> list[str]:
  if selection == "all":
    return ["cam1", "cam2", "cam3"]
  if selection == "both":
    return ["cam2", "cam3"]
  return [selection]


def load_summary(path: Path) -> dict:
  with path.open() as f:
    return json.load(f)


def tile_clip_area(tiles: list[dict], threshold: float) -> float:
  if not tiles:
    return -1.0
  return sum(1 for tile in tiles if float(tile.get("luma_clip_hi_frac", 0.0)) > threshold) / len(tiles)


def neutral_tiles(tiles: list[dict], max_clip: float, min_y: float, max_y: float) -> list[dict]:
  selected = []
  for tile in tiles:
    y_median = float(tile.get("y_median", -1.0))
    luma_clip = float(tile.get("luma_clip_hi_frac", 1.0))
    rgb = tile.get("rgb_median")
    if not isinstance(rgb, list) or len(rgb) != 3:
      continue
    if luma_clip <= max_clip and min_y <= y_median <= max_y:
      selected.append(tile)
  return selected


def median(values: list[float]) -> float:
  return float(statistics.median(values)) if values else -1.0


def wb_hint(tiles: list[dict]) -> dict:
  if not tiles:
    return {
      "tile_count": 0,
      "rgb_median": [-1.0, -1.0, -1.0],
      "relative_rgb_gain": [-1.0, -1.0, -1.0],
      "green_fixed_red_gain": -1.0,
      "green_fixed_blue_gain": -1.0,
    }

  reds = [float(tile["rgb_median"][0]) for tile in tiles]
  greens = [float(tile["rgb_median"][1]) for tile in tiles]
  blues = [float(tile["rgb_median"][2]) for tile in tiles]
  rgb = [median(reds), median(greens), median(blues)]
  target = sum(rgb) / 3.0
  gains = [target / channel if channel > 0.0 else -1.0 for channel in rgb]
  return {
    "tile_count": len(tiles),
    "rgb_median": rgb,
    "relative_rgb_gain": gains,
    "green_fixed_red_gain": rgb[1] / rgb[0] if rgb[0] > 0.0 else -1.0,
    "green_fixed_blue_gain": rgb[1] / rgb[2] if rgb[2] > 0.0 else -1.0,
  }


def print_cam(cam: str, data: dict, args: argparse.Namespace) -> None:
  cam_data = data.get("cameras", {}).get(cam, {})
  image = cam_data.get("image", {})
  tiles = image.get("tiles", [])
  if not isinstance(tiles, list):
    tiles = []

  frames = cam_data.get("debug_frames", "?")
  fps = cam_data.get("median_fps", None)
  fps_text = "?" if fps is None else f"{float(fps):.2f}"
  print(f"{cam}: frames={frames} fps={fps_text}")
  print(
    f"  image: y_median={float(image.get('y_median', -1.0)):.1f} "
    f"y_range={float(image.get('y_range_p99_p01', -1.0)):.1f} "
    f"luma_clip={float(image.get('luma_clip_hi_frac', -1.0)):.3f} "
    f"rgb_spread={float(image.get('rgb_median_spread', -1.0)):.1f} "
    f"chroma={float(image.get('mean_chroma', -1.0)):.2f}"
  )

  area10 = float(image.get("tile_luma_clip_hi_area_frac_gt_10pct", tile_clip_area(tiles, 0.10)))
  area50 = float(image.get("tile_luma_clip_hi_area_frac_gt_50pct", tile_clip_area(tiles, 0.50)))
  print(
    f"  clipping: tile_max={float(image.get('tile_max_luma_clip_hi_frac', -1.0)):.3f} "
    f"area_gt10pct={area10:.2f} area_gt50pct={area50:.2f}"
  )

  for tile in sorted(tiles, key=lambda t: float(t.get("luma_clip_hi_frac", 0.0)), reverse=True)[:args.top_tiles]:
    rgb = tile.get("rgb_median", [-1, -1, -1])
    print(
      f"  clipped_tile: r{int(tile.get('row', -1))}c{int(tile.get('col', -1))} "
      f"clip={float(tile.get('luma_clip_hi_frac', 0.0)):.3f} "
      f"y={float(tile.get('y_median', -1.0)):.1f} "
      f"rgb={','.join(str(int(round(float(x)))) for x in rgb)}"
    )

  selected = neutral_tiles(tiles, args.max_neutral_clip, args.min_neutral_y, args.max_neutral_y)
  hint = wb_hint(selected)
  rgb_text = ",".join(f"{channel:.1f}" for channel in hint["rgb_median"])
  gain_text = ",".join(f"{gain:.3f}" for gain in hint["relative_rgb_gain"])
  print(
    f"  wb_hint: neutral_tiles={hint['tile_count']} rgb_median={rgb_text} "
    f"relative_rgb_gain={gain_text} "
    f"green_fixed_red={hint['green_fixed_red_gain']:.3f} "
    f"green_fixed_blue={hint['green_fixed_blue_gain']:.3f}"
  )
  if hint["tile_count"] < args.min_neutral_tiles:
    print(
      f"  wb_hint_warning: only {hint['tile_count']} usable non-clipped tiles; "
      "do not bake WB from this scene"
    )


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("run_dir", type=Path, help="output directory from camerad_capture_latest.py")
  parser.add_argument("--cam", choices=("cam1", "cam2", "cam3", "both", "all"), default="both")
  parser.add_argument("--summary-json", type=Path, help=f"default is RUN_DIR/{SUMMARY_FILE}")
  parser.add_argument("--top-tiles", type=int, default=5)
  parser.add_argument("--max-neutral-clip", type=float, default=0.02)
  parser.add_argument("--min-neutral-y", type=float, default=45.0)
  parser.add_argument("--max-neutral-y", type=float, default=210.0)
  parser.add_argument("--min-neutral-tiles", type=int, default=6)
  args = parser.parse_args()

  summary_path = args.summary_json or args.run_dir / SUMMARY_FILE
  data = load_summary(summary_path)
  print(f"summary={summary_path}")
  print(f"passed={bool(data.get('passed', False))}")
  print(f"hardware_path_passed={bool(data.get('hardware_path_passed', False))}")
  print(f"image_quality_passed={bool(data.get('image_quality_passed', False))}")
  category_passed = data.get("category_passed", {})
  if category_passed:
    cats = " ".join(f"{key}={value}" for key, value in sorted(category_passed.items()))
    print(f"categories: {cats}")
  failures = data.get("failure_details", [])
  for failure in failures:
    print(f"failure[{failure.get('category', 'unknown')}]: {failure.get('message', '')}")

  for cam in camera_list(args.cam):
    print_cam(cam, data, args)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
