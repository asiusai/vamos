#!/usr/bin/env python3
"""Sweep CAM2/CAM3 hardware VFE tuning candidates and summarize results."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from run_vfe_acceptance import camera_extract, load_json


SUMMARY_FILE = "vfe-tuning-sweep-summary.json"
DEFAULT_MAX_CANDIDATES = 12


@dataclass(frozen=True)
class EnvCombo:
  name: str
  env: tuple[str, ...]


@dataclass(frozen=True)
class Candidate:
  name: str
  target_grey: float
  env: tuple[str, ...]


def default_out_dir() -> Path:
  stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
  return Path(f"/tmp/dragon_os04_bench/vfe-tuning-sweep-{stamp}")


def slug(text: str) -> str:
  clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", text.strip())
  clean = clean.strip("-._")
  return clean or "candidate"


def target_slug(value: float) -> str:
  if value == 0.0:
    return "tg-default"
  text = f"{value:.4f}".rstrip("0").rstrip(".").replace(".", "p")
  return f"tg{text}"


def parse_env_combo(spec: str) -> EnvCombo:
  name, sep, env_spec = spec.partition(":")
  name = slug(name)
  env: list[str] = []
  if sep:
    for token in re.split(r"[;,]", env_spec):
      token = token.strip()
      if not token:
        continue
      if "=" not in token:
        raise ValueError(f"env combo {spec!r} has invalid token {token!r}; expected NAME=VALUE")
      key, value = token.split("=", 1)
      if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise ValueError(f"env combo {spec!r} has invalid env name {key!r}")
      if value == "":
        raise ValueError(f"env combo {spec!r} has empty value for {key!r}")
      env.append(f"{key}={value}")
  return EnvCombo(name=name, env=tuple(env))


def build_candidates(target_greys: list[float], env_combos: list[EnvCombo]) -> list[Candidate]:
  candidates: list[Candidate] = []
  for combo in env_combos:
    for target_grey in target_greys:
      name = slug(f"{combo.name}-{target_slug(target_grey)}")
      candidates.append(Candidate(name=name, target_grey=target_grey, env=combo.env))
  return candidates


def build_capture_cmd(args: argparse.Namespace, candidate: Candidate, out_dir: Path) -> list[str]:
  cmd = [
    sys.executable,
    str(Path(__file__).resolve().with_name("camerad_capture_latest.py")),
    "--openpilot-dir", args.openpilot_dir,
    "--cam", "both",
    "--out-dir", str(out_dir),
    "--settle", str(args.settle),
    "--monitor-duration", str(args.monitor_duration),
    "--target-grey", str(candidate.target_grey),
    "--validate-vfe",
    "--validate-quality-profile", args.profile,
    "--check-dmesg",
    "--log-awb",
    "--log-ae",
    "--pull-timeout", str(args.pull_timeout),
  ]
  if args.require_ae_rgb_clip_guard:
    cmd.extend([
      "--validate-ae-rgb-clip-guard",
      "--validate-min-ae-samples", str(args.min_ae_samples),
      "--validate-min-ae-rgb-clip", str(args.min_ae_rgb_clip),
      "--validate-min-ae-ev-cap", str(args.min_ae_ev_cap),
    ])
  for env in candidate.env:
    cmd.extend(["--env", env])
  return cmd


def run_cmd(cmd: list[str], dry_run: bool) -> int:
  print("+", " ".join(cmd), flush=True)
  if dry_run:
    return 0
  return subprocess.run(cmd, check=False).returncode


def numeric(value: object, default: float) -> float:
  return float(value) if isinstance(value, (int, float)) else default


def candidate_sort_key(result: dict) -> tuple:
  cameras = result.get("cameras", {})
  clip_area = 0.0
  color_noise = 0.0
  luma_error = 0.0
  metric_count = 0
  for cam in ("cam2", "cam3"):
    cam_data = cameras.get(cam, {})
    clip_area += numeric(cam_data.get("tile_luma_clip_hi_area_frac_gt_10pct"), 1.0)
    clip_area += numeric(cam_data.get("tile_luma_clip_hi_area_frac_gt_50pct"), 1.0)
    color_noise += numeric(cam_data.get("rgb_median_spread"), 60.0) / 60.0
    color_noise += numeric(cam_data.get("uv_hf_abs_mean"), 6.25) / 6.25
    y_median = cam_data.get("y_median")
    if isinstance(y_median, (int, float)):
      luma_error += abs(float(y_median) - 115.0) / 115.0
      metric_count += 1
  if metric_count == 0:
    luma_error = 2.0
  failures = result.get("failures") or []
  return (
    not bool(result.get("passed")),
    not bool(result.get("hardware_path_passed")),
    not bool(result.get("image_quality_passed")),
    len(failures),
    clip_area,
    color_noise,
    luma_error,
    result.get("name", ""),
  )


def summarize_result(candidate: Candidate, out_dir: Path, returncode: int) -> dict:
  summary_json = out_dir / "latest-camerad-vfe-summary.json"
  capture_summary = load_json(summary_json)
  result = {
    "name": candidate.name,
    "out_dir": str(out_dir),
    "target_grey": candidate.target_grey,
    "env": list(candidate.env),
    "returncode": returncode,
    "summary_json": str(summary_json),
    "passed": bool(capture_summary.get("passed", False)) if capture_summary else False,
    "hardware_path_passed": bool(capture_summary.get("hardware_path_passed", False)) if capture_summary else False,
    "image_quality_passed": bool(capture_summary.get("image_quality_passed", False)) if capture_summary else False,
    "failures": capture_summary.get("failures", []) if capture_summary else ["missing capture summary"],
    "cameras": camera_extract(capture_summary),
  }
  result["sort_key"] = list(candidate_sort_key(result))
  return result


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--openpilot-dir", default="/data/openpilot_hw_vfe")
  parser.add_argument("--out-dir", type=Path, default=None)
  parser.add_argument("--profile", default="daylight-road", choices=("bench", "road", "road-spatial", "daylight-road"))
  parser.add_argument("--settle", type=float, default=7.0)
  parser.add_argument("--monitor-duration", type=float, default=15.0)
  parser.add_argument("--target-grey", dest="target_greys", type=float, action="append",
                      help="target grey to test; repeat. 0 means camerad default. default: 0")
  parser.add_argument(
    "--env-combo",
    action="append",
    help=(
      "candidate env combo as NAME or NAME:ENV=VALUE[,ENV=VALUE...]. "
      "Repeat to cross with --target-grey. Default: default"
    ),
  )
  parser.add_argument("--pull-timeout", type=float, default=60.0)
  parser.add_argument("--require-ae-rgb-clip-guard", action=argparse.BooleanOptionalAction, default=True)
  parser.add_argument("--min-ae-samples", type=int, default=3)
  parser.add_argument("--min-ae-rgb-clip", type=float, default=0.079)
  parser.add_argument("--min-ae-ev-cap", type=float, default=0.05)
  parser.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES)
  parser.add_argument("--dry-run", action="store_true")
  args = parser.parse_args()

  target_greys = args.target_greys if args.target_greys else [0.0]
  if any(value < 0.0 for value in target_greys):
    parser.error("--target-grey values must be non-negative")
  if args.monitor_duration <= 0.0:
    parser.error("--monitor-duration must be positive")
  if args.pull_timeout <= 0.0:
    parser.error("--pull-timeout must be positive")

  try:
    env_combos = [parse_env_combo(spec) for spec in (args.env_combo or ["default"])]
  except ValueError as e:
    parser.error(str(e))

  candidates = build_candidates(target_greys, env_combos)
  if len(candidates) > args.max_candidates:
    parser.error(f"{len(candidates)} candidates exceeds --max-candidates {args.max_candidates}")

  out_dir = args.out_dir or default_out_dir()
  out_dir.mkdir(parents=True, exist_ok=True)

  summary = {
    "out_dir": str(out_dir),
    "openpilot_dir": args.openpilot_dir,
    "profile": args.profile,
    "dry_run": bool(args.dry_run),
    "candidate_count": len(candidates),
    "candidates": [],
    "note": (
      "Use this on a real daylight-road scene. Ranking is a machine hint only; "
      "final acceptance still requires run_vfe_acceptance.py with a human visual pass."
    ),
  }

  for candidate in candidates:
    candidate_dir = out_dir / candidate.name
    cmd = build_capture_cmd(args, candidate, candidate_dir)
    rc = run_cmd(cmd, args.dry_run)
    result = summarize_result(candidate, candidate_dir, rc)
    result["command"] = cmd
    summary["candidates"].append(result)

  ranked = sorted(summary["candidates"], key=candidate_sort_key)
  summary["ranked_candidates"] = [result["name"] for result in ranked]
  summary["best_candidate"] = ranked[0]["name"] if ranked else None

  summary_path = out_dir / SUMMARY_FILE
  summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
  print(f"summary_json: {summary_path}")
  print(f"best_candidate: {summary['best_candidate']}")
  return 0 if all(candidate["returncode"] == 0 for candidate in summary["candidates"]) else 1


if __name__ == "__main__":
  raise SystemExit(main())
