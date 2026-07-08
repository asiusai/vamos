#!/usr/bin/env python3
"""Sweep CAM2/CAM3 hardware VFE tuning candidates and summarize results."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from run_vfe_acceptance import (
  DEFAULT_MIN_FINAL_MODELD_DURATION,
  DEFAULT_MIN_FINAL_SNAPSHOT_DURATION,
  SUMMARY_FILE as ACCEPTANCE_SUMMARY_FILE,
  camera_extract,
  load_json,
)


SUMMARY_FILE = "vfe-tuning-sweep-summary.json"
CONTACT_SHEET_FILE = "vfe-tuning-sweep-contact.jpg"
BEST_ACCEPTANCE_SCRIPT_FILE = "run-best-vfe-acceptance.sh"
BEST_FINALIZE_SCRIPT_FILE = "finalize-best-vfe-acceptance.sh"
DEFAULT_MAX_CANDIDATES = 12
SWEEP_PRESETS = {
  "manual": {
    "target_greys": None,
    "env_combos": None,
    "description": "use explicitly supplied --target-grey and --env-combo values",
  },
  "os04-daylight-v1": {
    "target_greys": (0.0, 0.45),
    "env_combos": (
      "default",
      "gamma18:ASIUS_CAM_GAMMA_K=18",
      "gamma20:ASIUS_CAM_GAMMA_K=20",
      "split20-18:ASIUS_PHYS_CAM2_GAMMA_K=20,ASIUS_PHYS_CAM3_GAMMA_K=18",
    ),
    "description": "CAM2/CAM3 OS04 daylight-road candidates from current hardware-VFE bench evidence",
  },
}

CAMERA_IMAGE_FILES = {
  "cam2": "latest-camerad-road.jpg",
  "cam3": "latest-camerad-wide.jpg",
}


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


def preset_help() -> str:
  return "; ".join(
    f"{name}: {preset['description']}" for name, preset in SWEEP_PRESETS.items()
  )


def select_sweep_inputs(args: argparse.Namespace) -> tuple[list[float], list[str]]:
  target_greys = list(args.target_greys or [])
  env_combo_specs = list(args.env_combo or [])
  preset = SWEEP_PRESETS[args.preset]

  if not target_greys and preset["target_greys"] is not None:
    target_greys = list(preset["target_greys"])
  if not env_combo_specs and preset["env_combos"] is not None:
    env_combo_specs = list(preset["env_combos"])

  if not target_greys:
    target_greys = [0.0]
  if not env_combo_specs:
    env_combo_specs = ["default"]
  return target_greys, env_combo_specs


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


def build_acceptance_cmd(args: argparse.Namespace, candidate: Candidate, out_dir: Path) -> list[str]:
  cmd = [
    sys.executable,
    str(Path(__file__).resolve().with_name("run_vfe_acceptance.py")),
    "--openpilot-dir", args.openpilot_dir,
    "--out-dir", str(out_dir),
    "--snapshot-profile", "daylight-road",
    "--snapshot-settle", str(args.acceptance_snapshot_settle),
    "--snapshot-monitor-duration", str(args.acceptance_snapshot_duration),
    "--modeld-settle", str(args.acceptance_modeld_settle),
    "--modeld-duration", str(args.acceptance_modeld_duration),
    "--target-grey", str(candidate.target_grey),
    "--pull-timeout", str(args.pull_timeout),
  ]
  if args.require_ae_rgb_clip_guard:
    cmd.extend([
      "--min-ae-samples", str(args.min_ae_samples),
      "--min-ae-rgb-clip", str(args.min_ae_rgb_clip),
      "--min-ae-ev-cap", str(args.min_ae_ev_cap),
    ])
  else:
    cmd.append("--no-require-ae-rgb-clip-guard")
  for env in candidate.env:
    cmd.extend(["--env", env])
  return cmd


def build_finalize_cmd_template(out_dir: Path) -> list[str]:
  return [
    sys.executable,
    str(Path(__file__).resolve().with_name("run_vfe_acceptance.py")),
    "--finalize-existing-summary", str(out_dir / ACCEPTANCE_SUMMARY_FILE),
    "--visual-check-pass",
    "--visual-check-scene", "daylight-road",
    "--visual-check-note", "<human-review-note>",
    "--visual-check-montage-sha256", "<reviewed-montage-sha256>",
    "--require-final-acceptance",
  ]


def executable_script(path: Path, text: str) -> str:
  path.write_text(text)
  path.chmod(0o755)
  return str(path)


def build_acceptance_script_text(command: list[str]) -> str:
  return (
    "#!/usr/bin/env bash\n"
    "set -euo pipefail\n\n"
    "# Full CAM2/CAM3 hardware VFE acceptance for the best-ranked sweep candidate.\n"
    f"exec {shlex.join(command)}\n"
  )


def build_finalize_script_text(acceptance_out_dir: Path) -> str:
  command_prefix = [
    sys.executable,
    str(Path(__file__).resolve().with_name("run_vfe_acceptance.py")),
    "--finalize-existing-summary", str(acceptance_out_dir / ACCEPTANCE_SUMMARY_FILE),
    "--visual-check-pass",
    "--visual-check-scene", "daylight-road",
    "--visual-check-note",
  ]
  command_suffix = [
    "--visual-check-montage-sha256",
    "--require-final-acceptance",
  ]
  return (
    "#!/usr/bin/env bash\n"
    "set -euo pipefail\n\n"
    "if [[ $# -lt 2 ]]; then\n"
    '  echo "usage: $0 <reviewed-montage-sha256> <human-review-note>" >&2\n'
    "  exit 2\n"
    "fi\n\n"
    'reviewed_montage_sha256="$1"\n'
    "shift\n"
    'human_review_note="$*"\n\n'
    "# Finalize the existing acceptance summary after reviewing /tmp/asius-cams-latest.jpg.\n"
    f"exec {shlex.join(command_prefix)} \"$human_review_note\" "
    f"{shlex.join(command_suffix[:1])} \"$reviewed_montage_sha256\" {shlex.join(command_suffix[1:])}\n"
  )


def write_best_candidate_scripts(out_dir: Path, best_result: dict | None) -> dict:
  if not best_result:
    return {
      "acceptance_script": None,
      "finalize_script": None,
    }

  acceptance_script = executable_script(
    out_dir / BEST_ACCEPTANCE_SCRIPT_FILE,
    build_acceptance_script_text(best_result["acceptance_command"]),
  )
  finalize_script = executable_script(
    out_dir / BEST_FINALIZE_SCRIPT_FILE,
    build_finalize_script_text(Path(str(best_result["acceptance_out_dir"]))),
  )
  return {
    "acceptance_script": acceptance_script,
    "finalize_script": finalize_script,
  }


def run_cmd(cmd: list[str], dry_run: bool) -> int:
  print("+", " ".join(cmd), flush=True)
  if dry_run:
    return 0
  return subprocess.run(cmd, check=False).returncode


def numeric(value: object, default: float) -> float:
  return float(value) if isinstance(value, (int, float)) else default


def candidate_quality_metrics(result: dict) -> dict[str, float]:
  cameras = result.get("cameras", {})
  clip_area = 0.0
  chroma_weakness = 0.0
  center_color_cast = 0.0
  luma_error = 0.0
  texture_noise = 0.0
  metric_count = 0
  for cam in ("cam2", "cam3"):
    cam_data = cameras.get(cam, {})
    clip_area += numeric(cam_data.get("tile_luma_clip_hi_area_frac_gt_10pct"), 1.0)
    clip_area += numeric(cam_data.get("tile_luma_clip_hi_area_frac_gt_50pct"), 1.0)
    chroma_weakness += max(0.0, 8.0 - numeric(cam_data.get("mean_chroma"), 0.0)) / 8.0
    center_color_cast += numeric(cam_data.get("max_uv_center_median_offset"), 32.0) / 32.0
    texture_noise += numeric(cam_data.get("rgb_median_spread"), 60.0) / 60.0
    texture_noise += numeric(cam_data.get("uv_hf_abs_mean"), 6.25) / 6.25
    y_median = cam_data.get("y_median")
    if isinstance(y_median, (int, float)):
      luma_error += abs(float(y_median) - 115.0) / 115.0
      metric_count += 1
  if metric_count == 0:
    luma_error = 2.0
  return {
    "clip_area": clip_area,
    "luma_error": luma_error,
    "color_defect": center_color_cast + chroma_weakness,
    "center_color_cast": center_color_cast,
    "chroma_weakness": chroma_weakness,
    "texture_noise": texture_noise,
  }


def candidate_sort_key(result: dict) -> tuple:
  metrics = candidate_quality_metrics(result)
  failures = result.get("failures") or []
  return (
    not bool(result.get("passed")),
    not bool(result.get("hardware_path_passed")),
    not bool(result.get("image_quality_passed")),
    len(failures),
    metrics["clip_area"],
    metrics["luma_error"],
    metrics["color_defect"],
    metrics["center_color_cast"],
    metrics["chroma_weakness"],
    metrics["texture_noise"],
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
  result["quality_metrics"] = candidate_quality_metrics(result)
  result["sort_key"] = list(candidate_sort_key(result))
  return result


def metric_text(value: object) -> str:
  if isinstance(value, float):
    return f"{value:.2f}"
  if isinstance(value, int):
    return str(value)
  return "n/a"


def candidate_label(result: dict) -> list[str]:
  lines = [
    result.get("name", "candidate"),
    f"pass={bool(result.get('passed'))} hw={bool(result.get('hardware_path_passed'))} iq={bool(result.get('image_quality_passed'))}",
    f"target={result.get('target_grey')} failures={len(result.get('failures') or [])}",
  ]
  for cam in ("cam2", "cam3"):
    cam_data = result.get("cameras", {}).get(cam, {})
    y = cam_data.get("y_median")
    rgb = cam_data.get("rgb_median_spread")
    uvhf = cam_data.get("uv_hf_abs_mean")
    chroma = cam_data.get("mean_chroma")
    center_uv = cam_data.get("max_uv_center_median_offset")
    clip10 = cam_data.get("tile_luma_clip_hi_area_frac_gt_10pct")
    clip50 = cam_data.get("tile_luma_clip_hi_area_frac_gt_50pct")
    raw = cam_data.get("latest_raw_match")
    lines.append(
      f"{cam}: y={metric_text(y)} rgb={metric_text(rgb)} "
      f"chroma={metric_text(chroma)} centerUV={metric_text(center_uv)} "
      f"uvhf={metric_text(uvhf)} clip={metric_text(clip10)}/{metric_text(clip50)} "
      f"raw={raw if raw is not None else 'n/a'}"
    )
  return lines


def load_candidate_image(result: dict, cam: str, width: int):
  from PIL import Image

  path = Path(str(result.get("out_dir", ""))) / CAMERA_IMAGE_FILES[cam]
  if not path.exists():
    return None
  image = Image.open(path).convert("RGB")
  ratio = width / image.width
  height = max(1, int(image.height * ratio))
  resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
  return image.resize((width, height), resampling)


def build_contact_sheet(ranked_results: list[dict], out_dir: Path, image_width: int = 420) -> Path | None:
  try:
    from PIL import Image, ImageDraw, ImageFont
  except ImportError:
    return None

  rows = []
  for result in ranked_results:
    cam2 = load_candidate_image(result, "cam2", image_width)
    cam3 = load_candidate_image(result, "cam3", image_width)
    if cam2 is None and cam3 is None:
      continue
    rows.append((result, cam2, cam3))
  if not rows:
    return None

  label_width = 360
  pad = 14
  row_gap = 18
  font = ImageFont.load_default()
  row_height = max(image.height for _, cam2, cam3 in rows for image in (cam2, cam3) if image is not None)
  width = label_width + pad * 4 + image_width * 2
  height = pad + len(rows) * row_height + (len(rows) - 1) * row_gap + pad
  sheet = Image.new("RGB", (width, height), "white")
  draw = ImageDraw.Draw(sheet)

  y = pad
  for result, cam2, cam3 in rows:
    x = pad
    label_lines = candidate_label(result)
    for line in label_lines:
      draw.text((x, y), line, fill="black", font=font)
      y += 14
    y -= 14 * len(label_lines)

    image_x = label_width + pad * 2
    if cam2 is not None:
      sheet.paste(cam2, (image_x, y))
      draw.text((image_x, y + cam2.height + 3), "CAM2 road", fill="black", font=font)
    image_x += image_width + pad
    if cam3 is not None:
      sheet.paste(cam3, (image_x, y))
      draw.text((image_x, y + cam3.height + 3), "CAM3 wide", fill="black", font=font)
    y += row_height + row_gap

  path = out_dir / CONTACT_SHEET_FILE
  sheet.save(path, "JPEG", quality=92)
  return path


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--openpilot-dir", default="/data/openpilot_hw_vfe")
  parser.add_argument("--out-dir", type=Path, default=None)
  parser.add_argument("--preset", choices=tuple(SWEEP_PRESETS), default="manual", help=preset_help())
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
  parser.add_argument("--acceptance-snapshot-settle", type=float, default=7.0)
  parser.add_argument("--acceptance-snapshot-duration", type=float, default=DEFAULT_MIN_FINAL_SNAPSHOT_DURATION)
  parser.add_argument("--acceptance-modeld-settle", type=float, default=7.0)
  parser.add_argument("--acceptance-modeld-duration", type=float, default=DEFAULT_MIN_FINAL_MODELD_DURATION)
  parser.add_argument("--dry-run", action="store_true")
  args = parser.parse_args()

  if args.monitor_duration <= 0.0:
    parser.error("--monitor-duration must be positive")
  if args.pull_timeout <= 0.0:
    parser.error("--pull-timeout must be positive")
  if args.acceptance_snapshot_settle <= 0.0:
    parser.error("--acceptance-snapshot-settle must be positive")
  if args.acceptance_snapshot_duration <= 0.0:
    parser.error("--acceptance-snapshot-duration must be positive")
  if args.acceptance_modeld_settle <= 0.0:
    parser.error("--acceptance-modeld-settle must be positive")
  if args.acceptance_modeld_duration <= 0.0:
    parser.error("--acceptance-modeld-duration must be positive")

  target_greys, env_combo_specs = select_sweep_inputs(args)
  if any(value < 0.0 for value in target_greys):
    parser.error("--target-grey values must be non-negative")

  try:
    env_combos = [parse_env_combo(spec) for spec in env_combo_specs]
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
    "preset": args.preset,
    "target_greys": target_greys,
    "env_combos": env_combo_specs,
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
    acceptance_dir = out_dir / f"acceptance-{candidate.name}"
    cmd = build_capture_cmd(args, candidate, candidate_dir)
    rc = run_cmd(cmd, args.dry_run)
    result = summarize_result(candidate, candidate_dir, rc)
    result["command"] = cmd
    result["acceptance_out_dir"] = str(acceptance_dir)
    result["acceptance_command"] = build_acceptance_cmd(args, candidate, acceptance_dir)
    result["finalize_command_template"] = build_finalize_cmd_template(acceptance_dir)
    summary["candidates"].append(result)

  ranked = sorted(summary["candidates"], key=candidate_sort_key)
  summary["ranked_candidates"] = [result["name"] for result in ranked]
  summary["best_candidate"] = ranked[0]["name"] if ranked else None
  summary["best_candidate_acceptance_command"] = ranked[0]["acceptance_command"] if ranked else None
  summary["best_candidate_finalize_command_template"] = ranked[0]["finalize_command_template"] if ranked else None
  scripts = write_best_candidate_scripts(out_dir, ranked[0] if ranked else None)
  summary["best_candidate_acceptance_script"] = scripts["acceptance_script"]
  summary["best_candidate_finalize_script"] = scripts["finalize_script"]
  contact_sheet = build_contact_sheet(ranked, out_dir)
  summary["contact_sheet"] = str(contact_sheet) if contact_sheet else None

  summary_path = out_dir / SUMMARY_FILE
  summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
  print(f"summary_json: {summary_path}")
  if contact_sheet:
    print(f"contact_sheet: {contact_sheet}")
  if summary["best_candidate_acceptance_script"]:
    print(f"best_acceptance_script: {summary['best_candidate_acceptance_script']}")
  if summary["best_candidate_finalize_script"]:
    print(f"best_finalize_script: {summary['best_candidate_finalize_script']}")
  print(f"best_candidate: {summary['best_candidate']}")
  return 0 if all(candidate["returncode"] == 0 for candidate in summary["candidates"]) else 1


if __name__ == "__main__":
  raise SystemExit(main())
