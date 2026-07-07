#!/usr/bin/env python3
"""Validate a CAM2/CAM3 camerad_capture_latest.py VFE PIX capture directory."""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path


LOG_FILE = "latest-camerad-dual.log"
VIPC_STATS_FILE = "latest-camerad-vipc-stats.json"
CPU_STATS_FILE = "latest-camerad-cpu-stats.json"
DMESG_LOG_FILE = "latest-camerad-dmesg.log"
SUMMARY_FILE = "latest-camerad-vfe-summary.json"

CAMERA_NUMS = {
  "cam1": "2",
  "cam2": "1",
  "cam3": "0",
}

STAT_FILES = {
  "cam1": "latest-camerad-driver-raw-stats.json",
  "cam2": "latest-camerad-road-raw-stats.json",
  "cam3": "latest-camerad-wide-raw-stats.json",
}

FATAL_LOG_PATTERNS = [
  "falling back to RDI",
  "NV12 sw debayer",
  "VFE PIX unavailable",
  "falling back to V4L2 MMAP CPU-copy path",
]

DMESG_FORBIDDEN_PATTERNS = [
  ("normal VFE PIX buffer-address spam", re.compile(r"\bpix buf\d+ addr0=", re.IGNORECASE)),
  ("VFE PIX stall/recovery warning", re.compile(r"\bvfe\d+ pix .*\b(stall|recovering)\b", re.IGNORECASE)),
  ("camera pipeline error/failure", re.compile(
    r"\b(camss|csiphy|csid|vfe|os04c10|cci|camera)\b.*"
    r"\b(error|failed|failure|fault|timeout|timed out)\b",
    re.IGNORECASE,
  )),
]

QUALITY_PROFILES = {
  "bench": {},
  "road": {
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
  },
  "road-spatial": {
    "min_y_median": 70.0,
    "max_y_median": 190.0,
    "min_y_range": 50.0,
    "max_y_clip_lo": 0.20,
    "max_y_clip_hi": 0.20,
    "max_luma_clip_hi": 0.08,
    "max_rgb_spread": 12.0,
    "max_uv_median_offset": 4.0,
    "max_uv_center_median_offset": 3.0,
    "min_mean_chroma": 6.0,
    "min_uv_abs": 4.0,
    "max_tile_uv_median_offset": 18.0,
    "max_tile_uv_median_range": 28.0,
    "max_tile_rgb_spread": 60.0,
    "max_uv_hf_abs_mean": 6.0,
  },
}


class Report:
  def __init__(self) -> None:
    self.failures: list[str] = []
    self.warnings: list[str] = []

  def pass_(self, text: str) -> None:
    print(f"PASS {text}")

  def fail(self, text: str) -> None:
    self.failures.append(text)
    print(f"FAIL {text}")

  def warn(self, text: str) -> None:
    self.warnings.append(text)
    print(f"WARN {text}")


def camera_list(selection: str) -> list[str]:
  if selection == "all":
    return ["cam1", "cam2", "cam3"]
  if selection == "both":
    return ["cam2", "cam3"]
  return [selection]


def load_json(path: Path, report: Report, label: str) -> dict | None:
  if not path.exists():
    report.fail(f"{label} missing: {path}")
    return None
  try:
    with path.open() as f:
      data = json.load(f)
  except (OSError, json.JSONDecodeError) as e:
    report.fail(f"{label} unreadable: {path}: {e}")
    return None
  report.pass_(f"{label} present: {path}")
  return data


def forbidden_dmesg_matches(dmesg_text: str) -> list[dict[str, str]]:
  matches: list[dict[str, str]] = []
  for line in dmesg_text.splitlines():
    for label, pattern in DMESG_FORBIDDEN_PATTERNS:
      if pattern.search(line):
        matches.append({
          "kind": label,
          "line": line,
        })
        break
  return matches


def frame_times(log_text: str, cam: str) -> list[float]:
  cam_num = CAMERA_NUMS[cam]
  times: list[float] = []
  pattern = re.compile(rf"^cam {re.escape(cam_num)} frame \d+ .* ts ([0-9.]+) ms .*VFE PIX", re.MULTILINE)
  for match in pattern.finditer(log_text):
    times.append(float(match.group(1)))
  return times


def validate_log(run_dir: Path, cams: list[str], args: argparse.Namespace, report: Report, summary: dict) -> str:
  log_path = run_dir / LOG_FILE
  if not log_path.exists():
    report.fail(f"log missing: {log_path}")
    return ""

  log_text = log_path.read_text(errors="replace")
  report.pass_(f"log present: {log_path}")
  summary["log"] = {
    "path": str(log_path),
    "fallback_markers": {},
  }

  for pattern in FATAL_LOG_PATTERNS:
    present = pattern in log_text
    summary["log"]["fallback_markers"][pattern] = present
    if pattern in log_text:
      report.fail(f"log contains forbidden fallback marker: {pattern}")
    else:
      report.pass_(f"log has no fallback marker: {pattern}")

  dmabuf_fallback = "REQBUFS DMABUF failed" in log_text
  summary["log"]["dmabuf_fallback"] = dmabuf_fallback
  if args.require_dmabuf and dmabuf_fallback:
    report.fail("log contains DMABUF fallback: REQBUFS DMABUF failed")
  elif args.require_dmabuf:
    report.pass_("log has no DMABUF fallback")

  for cam in cams:
    cam_num = CAMERA_NUMS[cam]
    cam_summary = summary["cameras"].setdefault(cam, {})
    mode_pattern = rf"cam {re.escape(cam_num)}: VIPC buffers created \(VFE PIX V4L2"
    has_vfe_pix_v4l2 = re.search(mode_pattern, log_text) is not None
    cam_summary["vfe_pix_v4l2"] = has_vfe_pix_v4l2
    if not has_vfe_pix_v4l2:
      report.fail(f"{cam}: missing VFE PIX V4L2 VIPC buffer creation")
    else:
      report.pass_(f"{cam}: VFE PIX V4L2 VIPC buffer creation found")

    if args.require_dmabuf:
      dmabuf_pattern = rf"cam {re.escape(cam_num)}: VIPC buffers created \(VFE PIX V4L2 DMABUF NV12"
      has_dmabuf_nv12 = re.search(dmabuf_pattern, log_text) is not None
      cam_summary["dmabuf_nv12"] = has_dmabuf_nv12
      if not has_dmabuf_nv12:
        report.fail(f"{cam}: missing VFE PIX V4L2 DMABUF NV12 mode")
      else:
        report.pass_(f"{cam}: VFE PIX V4L2 DMABUF NV12 mode found")

    times = frame_times(log_text, cam)
    cam_summary["debug_frames"] = len(times)
    if args.min_frames > 0:
      if len(times) < args.min_frames:
        report.fail(f"{cam}: only {len(times)} debug frames, expected >= {args.min_frames}")
      else:
        report.pass_(f"{cam}: {len(times)} debug frames >= {args.min_frames}")

    if len(times) >= 2:
      intervals = [b - a for a, b in zip(times, times[1:])]
      median_ms = statistics.median(intervals)
      fps = 1000.0 / median_ms if median_ms > 0 else 0.0
      slow_gaps = sum(1 for interval in intervals if interval > args.slow_gap_ms)
      cam_summary["median_fps"] = fps
      cam_summary["slow_gaps"] = slow_gaps
      if fps < args.min_fps:
        report.fail(f"{cam}: median FPS {fps:.2f} below {args.min_fps:.2f}")
      else:
        report.pass_(f"{cam}: median FPS {fps:.2f} >= {args.min_fps:.2f}")
      if slow_gaps > args.max_slow_gaps:
        report.fail(f"{cam}: slow gaps {slow_gaps} > {args.max_slow_gaps} over {args.slow_gap_ms:.1f} ms")
      else:
        report.pass_(f"{cam}: slow gaps {slow_gaps} <= {args.max_slow_gaps}")
    elif args.min_frames > 0:
      report.fail(f"{cam}: no usable VFE PIX debug frame timestamps; capture with --camerad-debug-frames")

  return log_text


def validate_vipc_stats(run_dir: Path, cams: list[str], args: argparse.Namespace, report: Report, summary: dict) -> None:
  data = load_json(run_dir / VIPC_STATS_FILE, report, "VIPC stats")
  if data is None:
    return
  summary["vipc_stats_path"] = str(run_dir / VIPC_STATS_FILE)

  for cam in cams:
    stats = data.get(cam)
    if not isinstance(stats, dict):
      report.fail(f"{cam}: missing VIPC stats entry")
      continue
    width = int(stats.get("width", 0))
    height = int(stats.get("height", 0))
    stride = int(stats.get("stride", 0))
    summary["cameras"].setdefault(cam, {})["vipc"] = {
      "width": width,
      "height": height,
      "stride": stride,
      "saved_frame_id": int(stats.get("saved_frame_id", 0)),
    }
    if width < args.min_width or height < args.min_height:
      report.fail(f"{cam}: VIPC size {width}x{height} below {args.min_width}x{args.min_height}")
    else:
      report.pass_(f"{cam}: VIPC size {width}x{height}")
    if stride < width:
      report.fail(f"{cam}: VIPC stride {stride} below width {width}")
    else:
      report.pass_(f"{cam}: VIPC stride {stride} >= width {width}")


def validate_cpu_stats(run_dir: Path, args: argparse.Namespace, report: Report, summary: dict) -> None:
  path = run_dir / CPU_STATS_FILE
  if not path.exists():
    if args.require_cpu_stats:
      report.fail(f"camerad CPU stats missing: {path}")
    return

  data = load_json(path, report, "camerad CPU stats")
  if data is None:
    return

  if not bool(data.get("available", False)):
    summary["cpu"] = {
      "path": str(path),
      "available": False,
    }
    report.fail("camerad CPU stats unavailable")
    return

  wall_seconds = float(data.get("wall_seconds", 0.0))
  cpu_seconds = float(data.get("cpu_seconds", -1.0))
  cpu_pct = float(data.get("single_core_cpu_pct", 999.0))
  summary["cpu"] = {
    "path": str(path),
    "available": True,
    "wall_seconds": wall_seconds,
    "cpu_seconds": cpu_seconds,
    "single_core_cpu_pct": cpu_pct,
  }

  if wall_seconds < args.min_cpu_sample_seconds:
    report.fail(f"camerad CPU sample {wall_seconds:.3f}s below {args.min_cpu_sample_seconds:.3f}s")
  else:
    report.pass_(f"camerad CPU sample {wall_seconds:.3f}s >= {args.min_cpu_sample_seconds:.3f}s")

  if cpu_seconds < 0.0:
    report.fail(f"camerad CPU seconds invalid: {cpu_seconds:.3f}")
  else:
    report.pass_(f"camerad CPU seconds {cpu_seconds:.3f}")

  if cpu_pct > args.max_camerad_cpu_pct:
    report.fail(f"camerad CPU {cpu_pct:.2f}% > {args.max_camerad_cpu_pct:.2f}%")
  else:
    report.pass_(f"camerad CPU {cpu_pct:.2f}% <= {args.max_camerad_cpu_pct:.2f}%")


def validate_image_stats(run_dir: Path, cams: list[str], args: argparse.Namespace, report: Report, summary: dict) -> None:
  if args.no_raw_stats:
    return

  for cam in cams:
    data = load_json(run_dir / STAT_FILES[cam], report, f"{cam} raw stats")
    if data is None:
      continue

    width = int(data.get("width", 0))
    height = int(data.get("height", 0))
    if width < args.min_width or height < args.min_height:
      report.fail(f"{cam}: raw stats size {width}x{height} below {args.min_width}x{args.min_height}")
    else:
      report.pass_(f"{cam}: raw stats size {width}x{height}")

    u_median = float(data.get("u_median", 999.0))
    v_median = float(data.get("v_median", 999.0))
    u_center_median = float(data.get("u_center_median", 999.0))
    v_center_median = float(data.get("v_center_median", 999.0))
    max_uv_median_offset = max(abs(u_median - 128.0), abs(v_median - 128.0))
    max_uv_center_median_offset = max(abs(u_center_median - 128.0), abs(v_center_median - 128.0))
    image_summary = {
      "width": width,
      "height": height,
      "frame_id": int(data.get("frame_id", 0)),
      "y_median": float(data.get("y_median", -1.0)),
      "y_range_p99_p01": float(data.get("y_p99", -1.0)) - float(data.get("y_p01", 999.0)),
      "y_clip_lo_frac": float(data.get("y_clip_lo_frac", 1.0)),
      "y_clip_hi_frac": float(data.get("y_clip_hi_frac", 1.0)),
      "luma_clip_hi_frac": float(data.get("luma_clip_hi_frac", 1.0)),
      "rgb_median_spread": float(data.get("rgb_median_spread", 999.0)),
      "u_median": u_median,
      "v_median": v_median,
      "u_center_median": u_center_median,
      "v_center_median": v_center_median,
      "max_uv_median_offset": max_uv_median_offset,
      "max_uv_center_median_offset": max_uv_center_median_offset,
      "mean_chroma": float(data.get("mean_chroma", -1.0)),
      "uv_abs_mean": float(data.get("uv_abs_mean", -1.0)),
      "uv_hf_abs_mean": float(data.get("uv_hf_abs_mean", 999.0)),
      "tile_y_median_range": float(data.get("tile_y_median_range", 999.0)),
      "tile_uv_median_range": float(data.get("tile_uv_median_range", 999.0)),
      "tile_max_uv_median_offset": float(data.get("tile_max_uv_median_offset", 999.0)),
      "tile_p95_uv_median_offset": float(data.get("tile_p95_uv_median_offset", 999.0)),
      "tile_max_rgb_median_spread": float(data.get("tile_max_rgb_median_spread", 999.0)),
      "tile_p95_rgb_median_spread": float(data.get("tile_p95_rgb_median_spread", 999.0)),
      "tile_max_y_clip_hi_frac": float(data.get("tile_max_y_clip_hi_frac", -1.0)),
      "tile_p95_y_clip_hi_frac": float(data.get("tile_p95_y_clip_hi_frac", -1.0)),
      "tile_max_luma_clip_hi_frac": float(data.get("tile_max_luma_clip_hi_frac", -1.0)),
      "tile_p95_luma_clip_hi_frac": float(data.get("tile_p95_luma_clip_hi_frac", -1.0)),
    }
    if "tiles" in data:
      image_summary["tile_rows"] = int(data.get("tile_rows", 0))
      image_summary["tile_cols"] = int(data.get("tile_cols", 0))
      image_summary["tiles"] = data["tiles"]
    summary["cameras"].setdefault(cam, {})["image"] = image_summary

    checks = [
      ("y_median", image_summary["y_median"], args.min_y_median, args.max_y_median),
      ("y_range_p99_p01", image_summary["y_range_p99_p01"], args.min_y_range, 999.0),
      ("y_clip_lo_frac", image_summary["y_clip_lo_frac"], 0.0, args.max_y_clip_lo),
      ("y_clip_hi_frac", image_summary["y_clip_hi_frac"], 0.0, args.max_y_clip_hi),
      ("luma_clip_hi_frac", image_summary["luma_clip_hi_frac"], 0.0, args.max_luma_clip_hi),
      ("rgb_median_spread", image_summary["rgb_median_spread"], 0.0, args.max_rgb_spread),
      ("max_uv_median_offset", image_summary["max_uv_median_offset"], 0.0, args.max_uv_median_offset),
      ("max_uv_center_median_offset", image_summary["max_uv_center_median_offset"], 0.0, args.max_uv_center_median_offset),
      ("mean_chroma", image_summary["mean_chroma"], args.min_mean_chroma, 999.0),
      ("uv_abs_mean", image_summary["uv_abs_mean"], args.min_uv_abs, 999.0),
      ("tile_max_uv_median_offset", image_summary["tile_max_uv_median_offset"], 0.0, args.max_tile_uv_median_offset),
      ("tile_uv_median_range", image_summary["tile_uv_median_range"], 0.0, args.max_tile_uv_median_range),
      ("tile_max_rgb_median_spread", image_summary["tile_max_rgb_median_spread"], 0.0, args.max_tile_rgb_spread),
      ("uv_hf_abs_mean", image_summary["uv_hf_abs_mean"], 0.0, args.max_uv_hf_abs_mean),
    ]
    for name, value, low, high in checks:
      if value < low or value > high:
        report.fail(f"{cam}: {name}={value:.3f} outside [{low:.3f}, {high:.3f}]")
      else:
        report.pass_(f"{cam}: {name}={value:.3f} inside [{low:.3f}, {high:.3f}]")


def validate_dmesg(run_dir: Path, args: argparse.Namespace, report: Report, summary: dict) -> None:
  if not args.check_dmesg:
    return

  path = args.dmesg_log or (run_dir / DMESG_LOG_FILE)
  dmesg_summary = {
    "checked": True,
    "path": str(path),
    "forbidden_matches": [],
  }
  summary["dmesg"] = dmesg_summary

  if not path.exists():
    report.fail(f"dmesg log missing: {path}")
    return

  dmesg_text = path.read_text(errors="replace")
  matches = forbidden_dmesg_matches(dmesg_text)
  dmesg_summary["forbidden_matches"] = matches
  dmesg_summary["line_count"] = len(dmesg_text.splitlines())

  if len(matches) > args.max_dmesg_matches:
    report.fail(f"dmesg forbidden matches {len(matches)} > {args.max_dmesg_matches}")
    for match in matches[:5]:
      report.fail(f"dmesg {match['kind']}: {match['line']}")
  else:
    report.pass_(f"dmesg forbidden matches {len(matches)} <= {args.max_dmesg_matches}")


def apply_quality_profile(args: argparse.Namespace) -> dict[str, dict[str, float]]:
  profile = QUALITY_PROFILES[args.quality_profile]
  applied: dict[str, dict[str, float]] = {}
  for attr, target in profile.items():
    current = float(getattr(args, attr))
    if attr.startswith("min_"):
      value = max(current, target)
    elif attr.startswith("max_"):
      value = min(current, target)
    else:
      value = target
    setattr(args, attr, value)
    applied[attr] = {
      "requested": target,
      "applied": value,
    }
  return applied


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("run_dir", type=Path, help="output directory from camerad_capture_latest.py")
  parser.add_argument("--cam", choices=("cam1", "cam2", "cam3", "both", "all"), default="both")
  parser.add_argument(
    "--quality-profile",
    choices=tuple(QUALITY_PROFILES),
    default="bench",
    help="bench keeps broad bring-up thresholds; road adds stricter image-quality thresholds",
  )
  parser.add_argument("--require-dmabuf", action=argparse.BooleanOptionalAction, default=True)
  parser.add_argument("--min-frames", type=int, default=20, help="minimum DEBUG_FRAMES lines per selected camera")
  parser.add_argument("--min-fps", type=float, default=18.0)
  parser.add_argument("--slow-gap-ms", type=float, default=75.0)
  parser.add_argument("--max-slow-gaps", type=int, default=0)
  parser.add_argument("--require-cpu-stats", action="store_true", help="fail if latest-camerad-cpu-stats.json is missing")
  parser.add_argument("--min-cpu-sample-seconds", type=float, default=1.0)
  parser.add_argument("--max-camerad-cpu-pct", type=float, default=10.0, help="maximum camerad CPU use as percent of one core")
  parser.add_argument("--min-width", type=int, default=1280)
  parser.add_argument("--min-height", type=int, default=720)
  parser.add_argument("--no-raw-stats", action="store_true", help="skip raw-debug JSON image-stat checks")
  parser.add_argument("--min-y-median", type=float, default=20.0)
  parser.add_argument("--max-y-median", type=float, default=235.0)
  parser.add_argument("--min-y-range", type=float, default=30.0, help="minimum raw Y p99-p01 contrast range")
  parser.add_argument("--max-y-clip-lo", type=float, default=0.30)
  parser.add_argument("--max-y-clip-hi", type=float, default=0.30)
  parser.add_argument("--max-luma-clip-hi", type=float, default=0.30)
  parser.add_argument("--max-rgb-spread", type=float, default=50.0)
  parser.add_argument("--max-uv-median-offset", type=float, default=999.0, help="maximum absolute U/V full-frame median offset from 128")
  parser.add_argument("--max-uv-center-median-offset", type=float, default=999.0, help="maximum absolute U/V center median offset from 128")
  parser.add_argument("--min-mean-chroma", type=float, default=1.0)
  parser.add_argument("--min-uv-abs", type=float, default=1.0)
  parser.add_argument("--max-tile-uv-median-offset", type=float, default=999.0, help="maximum per-tile absolute U/V median offset from 128")
  parser.add_argument("--max-tile-uv-median-range", type=float, default=999.0, help="maximum range of per-tile U or V medians")
  parser.add_argument("--max-tile-rgb-spread", type=float, default=999.0, help="maximum per-tile RGB median channel spread")
  parser.add_argument("--max-uv-hf-abs-mean", type=float, default=999.0, help="maximum mean high-frequency U/V absolute delta")
  parser.add_argument("--check-dmesg", action="store_true", help=f"fail on CAMSS/VFE errors, stalls, or buffer-address spam in {DMESG_LOG_FILE}")
  parser.add_argument("--dmesg-log", type=Path, help=f"dmesg log to check; default is RUN_DIR/{DMESG_LOG_FILE}")
  parser.add_argument("--max-dmesg-matches", type=int, default=0)
  parser.add_argument("--summary-json", type=Path, help=f"write compact JSON summary; default is RUN_DIR/{SUMMARY_FILE}")
  args = parser.parse_args()
  applied_quality_profile = apply_quality_profile(args)

  report = Report()
  cams = camera_list(args.cam)
  summary = {
    "run_dir": str(args.run_dir),
    "cams": cams,
    "cameras": {},
    "thresholds": {
      "quality_profile": args.quality_profile,
      "applied_quality_profile": applied_quality_profile,
      "require_dmabuf": args.require_dmabuf,
      "min_frames": args.min_frames,
      "min_fps": args.min_fps,
      "slow_gap_ms": args.slow_gap_ms,
      "max_slow_gaps": args.max_slow_gaps,
      "require_cpu_stats": args.require_cpu_stats,
      "max_camerad_cpu_pct": args.max_camerad_cpu_pct,
      "min_y_median": args.min_y_median,
      "max_y_median": args.max_y_median,
      "min_y_range": args.min_y_range,
      "max_y_clip_lo": args.max_y_clip_lo,
      "max_y_clip_hi": args.max_y_clip_hi,
      "max_luma_clip_hi": args.max_luma_clip_hi,
      "max_rgb_spread": args.max_rgb_spread,
      "max_uv_median_offset": args.max_uv_median_offset,
      "max_uv_center_median_offset": args.max_uv_center_median_offset,
      "min_mean_chroma": args.min_mean_chroma,
      "min_uv_abs": args.min_uv_abs,
      "max_tile_uv_median_offset": args.max_tile_uv_median_offset,
      "max_tile_uv_median_range": args.max_tile_uv_median_range,
      "max_tile_rgb_spread": args.max_tile_rgb_spread,
      "max_uv_hf_abs_mean": args.max_uv_hf_abs_mean,
      "check_dmesg": args.check_dmesg,
      "max_dmesg_matches": args.max_dmesg_matches,
    },
  }
  if not args.run_dir.is_dir():
    report.fail(f"run directory missing: {args.run_dir}")
    return 1

  validate_log(args.run_dir, cams, args, report, summary)
  validate_vipc_stats(args.run_dir, cams, args, report, summary)
  validate_cpu_stats(args.run_dir, args, report, summary)
  validate_image_stats(args.run_dir, cams, args, report, summary)
  validate_dmesg(args.run_dir, args, report, summary)

  summary["passed"] = not report.failures
  summary["failures"] = report.failures
  summary["warnings"] = report.warnings
  summary_path = args.summary_json or (args.run_dir / SUMMARY_FILE)
  summary_path.parent.mkdir(parents=True, exist_ok=True)
  summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
  print(f"summary_json: {summary_path}")
  print(f"summary: failures={len(report.failures)} warnings={len(report.warnings)}")
  return 1 if report.failures else 0


if __name__ == "__main__":
  raise SystemExit(main())
