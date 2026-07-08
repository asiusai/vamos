#!/usr/bin/env python3
"""Summarize VFE capture health and scene-dependent image issues."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
import re


LOG_FILE = "latest-camerad-dual.log"
SUMMARY_FILE = "latest-camerad-vfe-summary.json"

CAMERA_NUMS = {
  "cam1": "2",
  "cam2": "1",
  "cam3": "0",
}

AE_LOG_PATTERN = re.compile(
  r"cam (?P<cam_num>\d+): OS04 AE "
  r"grey=(?P<grey>[-+0-9.eE]+) "
  r"target=(?P<target>[-+0-9.eE]+) "
  r"rgb_clip=(?P<rgb_clip>[-+0-9.eE]+) "
  r"cur_ev=(?P<cur_ev>[-+0-9.eE]+) "
  r"desired_ev=(?P<desired_ev>[-+0-9.eE]+) "
  r"unclipped_ev=(?P<unclipped_ev>[-+0-9.eE]+) "
  r"exp (?P<old_exp>\d+)->(?P<exp>\d+) "
  r"gain_idx (?P<old_gain>\d+)->(?P<gain>\d+) "
  r"gain (?P<gain_factor>[-+0-9.eE]+)",
)

AWB_LOG_PATTERN = re.compile(
  r"cam (?P<cam_num>\d+): OS04 AWB (?P<stable>stable )?"
  r"U=(?P<u>\d+) V=(?P<v>\d+) "
  r"samples=(?P<samples>\d+) neutral=(?P<neutral>\d+) "
  r"blue=0x(?P<blue>[0-9a-fA-F]+) red=0x(?P<red>[0-9a-fA-F]+)",
)


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


def parse_ae_samples(log_text: str, cam: str) -> list[dict[str, float | int]]:
  cam_num = CAMERA_NUMS[cam]
  samples = []
  for match in AE_LOG_PATTERN.finditer(log_text):
    if match.group("cam_num") != cam_num:
      continue
    samples.append({
      "grey": float(match.group("grey")),
      "target": float(match.group("target")),
      "rgb_clip": float(match.group("rgb_clip")),
      "cur_ev": float(match.group("cur_ev")),
      "desired_ev": float(match.group("desired_ev")),
      "unclipped_ev": float(match.group("unclipped_ev")),
      "exp": int(match.group("exp")),
      "gain": int(match.group("gain")),
      "gain_factor": float(match.group("gain_factor")),
    })
  return samples


def parse_awb_samples(log_text: str, cam: str) -> list[dict[str, int | bool]]:
  cam_num = CAMERA_NUMS[cam]
  samples = []
  for match in AWB_LOG_PATTERN.finditer(log_text):
    if match.group("cam_num") != cam_num:
      continue
    samples.append({
      "stable": match.group("stable") is not None,
      "u": int(match.group("u")),
      "v": int(match.group("v")),
      "samples": int(match.group("samples")),
      "neutral": int(match.group("neutral")),
      "blue": int(match.group("blue"), 16),
      "red": int(match.group("red"), 16),
    })
  return samples


def print_log_hints(cam: str, log_text: str, args: argparse.Namespace) -> None:
  if not log_text:
    return

  ae_samples = parse_ae_samples(log_text, cam)
  if ae_samples:
    window = ae_samples[-args.log_window:]
    last = ae_samples[-1]
    cap_samples = sum(
      1 for sample in ae_samples
      if float(sample["unclipped_ev"]) - float(sample["desired_ev"]) >= args.min_ae_ev_cap
    )
    print(
      f"  ae_log: samples={len(ae_samples)} "
      f"last_grey={float(last['grey']):.4f} target={float(last['target']):.4f} "
      f"clip={float(last['rgb_clip']):.4f} exp={int(last['exp'])} "
      f"gain_idx={int(last['gain'])} gain={float(last['gain_factor']):.3f} "
      f"cap_samples={cap_samples}"
    )
    print(
      f"  ae_log_window: n={len(window)} "
      f"grey_med={median([float(sample['grey']) for sample in window]):.4f} "
      f"clip_med={median([float(sample['rgb_clip']) for sample in window]):.4f} "
      f"exp_med={median([float(sample['exp']) for sample in window]):.1f} "
      f"gain_idx_med={median([float(sample['gain']) for sample in window]):.1f}"
    )

  awb_samples = parse_awb_samples(log_text, cam)
  if awb_samples:
    window = awb_samples[-args.log_window:]
    last = awb_samples[-1]
    blue_med = int(round(median([float(sample["blue"]) for sample in window])))
    red_med = int(round(median([float(sample["red"]) for sample in window])))
    print(
      f"  awb_log: samples={len(awb_samples)} "
      f"last_u={int(last['u'])} last_v={int(last['v'])} "
      f"last_blue=0x{int(last['blue']):x} last_red=0x{int(last['red']):x} "
      f"stable_last={bool(last['stable'])}"
    )
    print(
      f"  awb_log_window: n={len(window)} "
      f"u_med={median([float(sample['u']) for sample in window]):.1f} "
      f"v_med={median([float(sample['v']) for sample in window]):.1f} "
      f"blue_med=0x{blue_med:x} red_med=0x{red_med:x}"
    )


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


def print_cam(cam: str, data: dict, args: argparse.Namespace, log_text: str) -> None:
  cam_data = data.get("cameras", {}).get(cam, {})
  image = cam_data.get("image", {})
  vfe_setup = cam_data.get("vfe_setup", {})
  tiles = image.get("tiles", [])
  if not isinstance(tiles, list):
    tiles = []

  frames = cam_data.get("debug_frames", "?")
  fps = cam_data.get("median_fps", None)
  fps_text = "?" if fps is None else f"{float(fps):.2f}"
  print(f"{cam}: frames={frames} fps={fps_text}")
  if isinstance(vfe_setup, dict) and vfe_setup:
    vipc = vfe_setup.get("vipc", {})
    gamma = vfe_setup.get("gamma", {})
    awb_config = vfe_setup.get("awb_config", {})
    if isinstance(vipc, dict) and isinstance(gamma, dict):
      print(
        f"  vfe_setup: mode={vipc.get('mode', '?')} "
        f"{vipc.get('width', '?')}x{vipc.get('height', '?')} "
        f"stride={vipc.get('stride', '?')} "
        f"regs={vfe_setup.get('poststart_reg_count', '?')} "
        f"gamma={float(gamma.get('g', -1.0)):.2f}/"
        f"{float(gamma.get('b', -1.0)):.2f}/"
        f"{float(gamma.get('r', -1.0)):.2f} "
        f"awb0=0x{int(awb_config.get('blue', 0)):x}/"
        f"0x{int(awb_config.get('red', 0)):x}"
      )
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
  print_log_hints(cam, log_text, args)


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
  parser.add_argument("--log-file", type=Path, help=f"default is RUN_DIR/{LOG_FILE}")
  parser.add_argument("--log-window", type=int, default=20, help="AE/AWB samples to use for rolling medians")
  parser.add_argument("--min-ae-ev-cap", type=float, default=0.05, help="unclipped_ev - desired_ev threshold for AE cap count")
  args = parser.parse_args()

  summary_path = args.summary_json or args.run_dir / SUMMARY_FILE
  data = load_summary(summary_path)
  log_path = args.log_file or args.run_dir / LOG_FILE
  log_text = log_path.read_text(errors="replace") if log_path.exists() else ""
  print(f"summary={summary_path}")
  if log_text:
    print(f"log={log_path}")
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
    print_cam(cam, data, args, log_text)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
