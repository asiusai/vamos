#!/usr/bin/env python3
"""Validate a CAM2/CAM3 camerad_capture_latest.py VFE PIX capture directory."""

from __future__ import annotations

import argparse
import hashlib
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

IMAGE_FILES = {
  "cam1": "latest-camerad-driver.jpg",
  "cam2": "latest-camerad-road.jpg",
  "cam3": "latest-camerad-wide.jpg",
}

RAW_IMAGE_FILES = {
  "cam1": "latest-camerad-driver-raw.jpg",
  "cam2": "latest-camerad-road-raw.jpg",
  "cam3": "latest-camerad-wide-raw.jpg",
}

FATAL_LOG_PATTERNS = [
  "falling back to RDI",
  "NV12 sw debayer",
  "VFE PIX unavailable",
  "falling back to V4L2 MMAP CPU-copy path",
]

AE_LOG_PATTERN = re.compile(
  r"cam (?P<cam_num>\d+): OS04 AE "
  r"grey=(?P<grey>[-+0-9.eE]+) "
  r"target=(?P<target>[-+0-9.eE]+) "
  r"rgb_clip=(?P<rgb_clip>[-+0-9.eE]+) "
  r"cur_ev=(?P<cur_ev>[-+0-9.eE]+) "
  r"desired_ev=(?P<desired_ev>[-+0-9.eE]+) "
  r"unclipped_ev=(?P<unclipped_ev>[-+0-9.eE]+)"
  r"(?: exp (?P<old_exp>\d+)->(?P<exp>\d+) "
  r"gain_idx (?P<old_gain>\d+)->(?P<gain>\d+) "
  r"gain (?P<gain_factor>[-+0-9.eE]+))?",
)

AWB_LOG_PATTERN = re.compile(
  r"cam (?P<cam_num>\d+): OS04 AWB (?P<stable>stable )?"
  r"U=(?P<u>\d+) V=(?P<v>\d+) "
  r"samples=(?P<samples>\d+) neutral=(?P<neutral>\d+) "
  r"blue=0x(?P<blue>[0-9a-fA-F]+) red=0x(?P<red>[0-9a-fA-F]+)",
)

VFE_SOURCE_FORMAT_PATTERN = re.compile(
  r"cam (?P<cam_num>\d+): VFE PIX source format "
  r"(?P<width>\d+)x(?P<height>\d+) code=0x(?P<code>[0-9a-fA-F]+)",
)

VFE_VIPC_PATTERN = re.compile(
  r"cam (?P<cam_num>\d+): VIPC buffers created "
  r"\((?P<mode>VFE PIX [^,]+), (?P<width>\d+)x(?P<height>\d+), "
  r"scale=(?P<scale>\d+), (?P<size>\d+) bytes, stride=(?P<stride>\d+)\)",
)

VFE_POSTSTART_REGS_PATTERN = re.compile(
  r"cam (?P<cam_num>\d+): wrote (?P<count>\d+) poststart overrides VFE regs",
)

VFE_GAMMA_PATTERN = re.compile(
  r"cam (?P<cam_num>\d+): wrote OS04 gamma DMI override "
  r"g=(?P<g>[-+0-9.eE]+) b=(?P<b>[-+0-9.eE]+) r=(?P<r>[-+0-9.eE]+)",
)

AWB_CONFIG_PATTERN = re.compile(
  r"cam (?P<cam_num>\d+): OS04 AWB enabled "
  r"start=(?P<start>\d+) interval=(?P<interval>\d+) "
  r"deadband=(?P<deadband>\d+) response=(?P<response>\d+) "
  r"step=(?P<step>\d+) y=(?P<y_min>\d+)-(?P<y_max>\d+) "
  r"chroma=(?P<chroma>\d+) min_samples=(?P<min_samples>\d+) "
  r"blue=0x(?P<blue>[0-9a-fA-F]+) red=0x(?P<red>[0-9a-fA-F]+) "
  r"range=0x(?P<range>[0-9a-fA-F]+)",
)

OS04_DIAG_WINDOW = 20

DMESG_FORBIDDEN_PATTERNS = [
  ("normal VFE PIX buffer-address spam", re.compile(r"\bpix buf\d+ addr0=", re.IGNORECASE)),
  ("VFE PIX stall/recovery warning", re.compile(r"\bvfe\d+ pix .*\b(stall|recovering)\b", re.IGNORECASE)),
  ("camera pipeline error/failure", re.compile(
    r"\b(camss|csiphy|csid|vfe|os04c10|cci|camera)\b.*"
    r"\b(error|failed|failure|fault|timeout|timed out)\b",
    re.IGNORECASE,
  )),
]

ROAD_SPATIAL_QUALITY_PROFILE = {
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
  "max_uv_hf_abs_mean": 6.25,
}

DAYLIGHT_ROAD_QUALITY_PROFILE = {
  **ROAD_SPATIAL_QUALITY_PROFILE,
  # Reject bench/indoor captures with large clipped light panels. A real road
  # scene may have a small clipped sky/sign tile, so gate on clipped area
  # instead of forbidding every clipped tile.
  "max_tile_luma_clip_hi_area_frac_gt_10pct": 0.12,
  "max_tile_luma_clip_hi_area_frac_gt_50pct": 0.04,
}

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
  "road-spatial": ROAD_SPATIAL_QUALITY_PROFILE,
  "daylight-road": DAYLIGHT_ROAD_QUALITY_PROFILE,
}


class Report:
  def __init__(self) -> None:
    self.failures: list[str] = []
    self.failure_details: list[dict[str, str]] = []
    self.warnings: list[str] = []

  def pass_(self, text: str) -> None:
    print(f"PASS {text}")

  def fail(self, text: str, category: str = "general") -> None:
    self.failures.append(text)
    self.failure_details.append({
      "category": category,
      "message": text,
    })
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


def load_json(path: Path, report: Report, label: str, category: str = "general") -> dict | None:
  if not path.exists():
    report.fail(f"{label} missing: {path}", category)
    return None
  try:
    with path.open() as f:
      data = json.load(f)
  except (OSError, json.JSONDecodeError) as e:
    report.fail(f"{label} unreadable: {path}: {e}", category)
    return None
  report.pass_(f"{label} present: {path}")
  return data


def file_sha256(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
      digest.update(chunk)
  return digest.hexdigest()


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


def parse_ae_samples(log_text: str, cam: str) -> list[dict]:
  cam_num = CAMERA_NUMS[cam]
  samples = []
  for match in AE_LOG_PATTERN.finditer(log_text):
    if match.group("cam_num") != cam_num:
      continue
    try:
      desired_ev = float(match.group("desired_ev"))
      unclipped_ev = float(match.group("unclipped_ev"))
      sample = {
        "grey": float(match.group("grey")),
        "target": float(match.group("target")),
        "rgb_clip": float(match.group("rgb_clip")),
        "cur_ev": float(match.group("cur_ev")),
        "desired_ev": desired_ev,
        "unclipped_ev": unclipped_ev,
        "ev_cap": unclipped_ev - desired_ev,
      }
      if match.group("exp") is not None:
        sample.update({
          "old_exp": int(match.group("old_exp")),
          "exp": int(match.group("exp")),
          "old_gain": int(match.group("old_gain")),
          "gain": int(match.group("gain")),
          "gain_factor": float(match.group("gain_factor")),
        })
    except ValueError:
      continue
    samples.append(sample)
  return samples


def parse_awb_samples(log_text: str, cam: str) -> list[dict]:
  cam_num = CAMERA_NUMS[cam]
  samples = []
  for match in AWB_LOG_PATTERN.finditer(log_text):
    if match.group("cam_num") != cam_num:
      continue
    try:
      samples.append({
        "stable": match.group("stable") is not None,
        "u": int(match.group("u")),
        "v": int(match.group("v")),
        "samples": int(match.group("samples")),
        "neutral": int(match.group("neutral")),
        "blue": int(match.group("blue"), 16),
        "red": int(match.group("red"), 16),
      })
    except ValueError:
      continue
  return samples


def median_field(samples: list[dict], key: str) -> float | None:
  values = [float(sample[key]) for sample in samples if key in sample and sample[key] is not None]
  return float(statistics.median(values)) if values else None


def summarize_ae_samples(samples: list[dict], args: argparse.Namespace) -> dict:
  active_samples = [
    sample for sample in samples
    if sample["rgb_clip"] >= args.min_ae_rgb_clip and sample["ev_cap"] >= args.min_ae_ev_cap
  ]
  window = samples[-OS04_DIAG_WINDOW:]
  window_summary = {
    "samples": len(window),
    "grey_median": median_field(window, "grey"),
    "target_median": median_field(window, "target"),
    "rgb_clip_median": median_field(window, "rgb_clip"),
    "ev_cap_median": median_field(window, "ev_cap"),
    "exp_median": median_field(window, "exp"),
    "gain_median": median_field(window, "gain"),
    "gain_factor_median": median_field(window, "gain_factor"),
  }
  return {
    "samples": len(samples),
    "guard_active_samples": len(active_samples),
    "max_rgb_clip": max((sample["rgb_clip"] for sample in samples), default=0.0),
    "max_ev_cap": max((sample["ev_cap"] for sample in samples), default=0.0),
    "last": samples[-1] if samples else None,
    "window": window_summary,
  }


def summarize_awb_samples(samples: list[dict]) -> dict:
  window = samples[-OS04_DIAG_WINDOW:]
  window_summary = {
    "samples": len(window),
    "u_median": median_field(window, "u"),
    "v_median": median_field(window, "v"),
    "blue_median": median_field(window, "blue"),
    "red_median": median_field(window, "red"),
  }
  return {
    "samples": len(samples),
    "stable_samples": sum(1 for sample in samples if sample.get("stable")),
    "last": samples[-1] if samples else None,
    "window": window_summary,
  }


def parse_vfe_setup(log_text: str, cam: str) -> dict:
  cam_num = CAMERA_NUMS[cam]
  setup: dict = {
    "camera_num": cam_num,
  }
  for match in VFE_SOURCE_FORMAT_PATTERN.finditer(log_text):
    if match.group("cam_num") == cam_num:
      setup["source_format"] = {
        "width": int(match.group("width")),
        "height": int(match.group("height")),
        "code": int(match.group("code"), 16),
        "code_hex": f"0x{int(match.group('code'), 16):x}",
      }

  for match in VFE_VIPC_PATTERN.finditer(log_text):
    if match.group("cam_num") == cam_num:
      setup["vipc"] = {
        "mode": match.group("mode"),
        "width": int(match.group("width")),
        "height": int(match.group("height")),
        "scale": int(match.group("scale")),
        "size_bytes": int(match.group("size")),
        "stride": int(match.group("stride")),
      }

  for match in VFE_POSTSTART_REGS_PATTERN.finditer(log_text):
    if match.group("cam_num") == cam_num:
      setup["poststart_reg_count"] = int(match.group("count"))

  for match in VFE_GAMMA_PATTERN.finditer(log_text):
    if match.group("cam_num") == cam_num:
      setup["gamma"] = {
        "g": float(match.group("g")),
        "b": float(match.group("b")),
        "r": float(match.group("r")),
      }

  for match in AWB_CONFIG_PATTERN.finditer(log_text):
    if match.group("cam_num") == cam_num:
      setup["awb_config"] = {
        "start": int(match.group("start")),
        "interval": int(match.group("interval")),
        "deadband": int(match.group("deadband")),
        "response": int(match.group("response")),
        "step": int(match.group("step")),
        "y_min": int(match.group("y_min")),
        "y_max": int(match.group("y_max")),
        "chroma": int(match.group("chroma")),
        "min_samples": int(match.group("min_samples")),
        "blue": int(match.group("blue"), 16),
        "red": int(match.group("red"), 16),
        "range": int(match.group("range"), 16),
      }
  return setup


def validate_log(run_dir: Path, cams: list[str], args: argparse.Namespace, report: Report, summary: dict) -> str:
  log_path = run_dir / LOG_FILE
  if not log_path.exists():
    report.fail(f"log missing: {log_path}", "transport")
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
      report.fail(f"log contains forbidden fallback marker: {pattern}", "transport")
    else:
      report.pass_(f"log has no fallback marker: {pattern}")

  dmabuf_fallback = "REQBUFS DMABUF failed" in log_text
  summary["log"]["dmabuf_fallback"] = dmabuf_fallback
  if args.require_dmabuf and dmabuf_fallback:
    report.fail("log contains DMABUF fallback: REQBUFS DMABUF failed", "transport")
  elif args.require_dmabuf:
    report.pass_("log has no DMABUF fallback")

  for cam in cams:
    cam_num = CAMERA_NUMS[cam]
    cam_summary = summary["cameras"].setdefault(cam, {})
    mode_pattern = rf"cam {re.escape(cam_num)}: VIPC buffers created \(VFE PIX V4L2"
    has_vfe_pix_v4l2 = re.search(mode_pattern, log_text) is not None
    cam_summary["vfe_pix_v4l2"] = has_vfe_pix_v4l2
    if not has_vfe_pix_v4l2:
      report.fail(f"{cam}: missing VFE PIX V4L2 VIPC buffer creation", "transport")
    else:
      report.pass_(f"{cam}: VFE PIX V4L2 VIPC buffer creation found")

    if args.require_dmabuf:
      dmabuf_pattern = rf"cam {re.escape(cam_num)}: VIPC buffers created \(VFE PIX V4L2 DMABUF NV12"
      has_dmabuf_nv12 = re.search(dmabuf_pattern, log_text) is not None
      cam_summary["dmabuf_nv12"] = has_dmabuf_nv12
      if not has_dmabuf_nv12:
        report.fail(f"{cam}: missing VFE PIX V4L2 DMABUF NV12 mode", "transport")
      else:
        report.pass_(f"{cam}: VFE PIX V4L2 DMABUF NV12 mode found")

    times = frame_times(log_text, cam)
    cam_summary["debug_frames"] = len(times)
    if args.min_frames > 0:
      if len(times) < args.min_frames:
        report.fail(f"{cam}: only {len(times)} debug frames, expected >= {args.min_frames}", "transport")
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
        report.fail(f"{cam}: median FPS {fps:.2f} below {args.min_fps:.2f}", "transport")
      else:
        report.pass_(f"{cam}: median FPS {fps:.2f} >= {args.min_fps:.2f}")
      if slow_gaps > args.max_slow_gaps:
        report.fail(f"{cam}: slow gaps {slow_gaps} > {args.max_slow_gaps} over {args.slow_gap_ms:.1f} ms", "transport")
      else:
        report.pass_(f"{cam}: slow gaps {slow_gaps} <= {args.max_slow_gaps}")
    elif args.min_frames > 0:
      report.fail(f"{cam}: no usable VFE PIX debug frame timestamps; capture with --camerad-debug-frames", "transport")

  return log_text


def summarize_vfe_setup_log(log_text: str, cams: list[str], report: Report, summary: dict) -> None:
  if not log_text:
    return

  vfe_setup = {
    "cameras": {},
  }
  summary["vfe_setup"] = vfe_setup
  camera_summaries = summary.setdefault("cameras", {})

  for cam in cams:
    cam_setup = parse_vfe_setup(log_text, cam)
    vfe_setup["cameras"][cam] = cam_setup
    camera_summaries.setdefault(cam, {})["vfe_setup"] = cam_setup
    if "vipc" in cam_setup and "gamma" in cam_setup:
      report.pass_(
        f"{cam}: VFE setup parsed mode={cam_setup['vipc']['mode']} "
        f"gamma_g={cam_setup['gamma']['g']:.2f}"
      )
    elif "vipc" in cam_setup:
      report.pass_(f"{cam}: VFE setup parsed mode={cam_setup['vipc']['mode']}")


def validate_ae_rgb_clip_guard(log_text: str, cams: list[str], args: argparse.Namespace, report: Report, summary: dict) -> None:
  if not log_text:
    if args.require_ae_rgb_clip_guard:
      report.fail("AE RGB clip guard required but log is unavailable", "ae")
    return

  ae_summary = {
    "required": bool(args.require_ae_rgb_clip_guard),
    "min_samples": args.min_ae_samples,
    "min_rgb_clip": args.min_ae_rgb_clip,
    "min_ev_cap": args.min_ae_ev_cap,
    "guard_active_samples": 0,
    "cameras": {},
  }
  summary["ae"] = ae_summary
  camera_summaries = summary.setdefault("cameras", {})

  any_guard_active = False
  for cam in cams:
    cam_num = CAMERA_NUMS[cam]
    samples = parse_ae_samples(log_text, cam)
    cam_summary = summarize_ae_samples(samples, args)
    cam_summary["camera_num"] = cam_num
    ae_summary["cameras"][cam] = cam_summary
    camera_summaries.setdefault(cam, {})["ae"] = cam_summary
    ae_summary["guard_active_samples"] += cam_summary["guard_active_samples"]
    any_guard_active = any_guard_active or bool(cam_summary["guard_active_samples"])

    if args.require_ae_rgb_clip_guard:
      if len(samples) < args.min_ae_samples:
        report.fail(f"{cam}: only {len(samples)} OS04 AE samples, expected >= {args.min_ae_samples}", "ae")
      else:
        report.pass_(f"{cam}: {len(samples)} OS04 AE samples >= {args.min_ae_samples}")
    elif samples:
      report.pass_(f"{cam}: {len(samples)} OS04 AE samples parsed")

  if not args.require_ae_rgb_clip_guard:
    return

  if any_guard_active:
    report.pass_(
      f"AE RGB clip guard active: {ae_summary['guard_active_samples']} samples "
      f"with rgb_clip >= {args.min_ae_rgb_clip:.4f} and EV cap >= {args.min_ae_ev_cap:.3f}"
    )
  else:
    report.fail(
      "AE RGB clip guard never capped EV in the captured scene "
      f"(need rgb_clip >= {args.min_ae_rgb_clip:.4f} and EV cap >= {args.min_ae_ev_cap:.3f})",
      "ae",
    )


def summarize_awb_log(log_text: str, cams: list[str], report: Report, summary: dict) -> None:
  if not log_text:
    return

  awb_summary = {
    "cameras": {},
  }
  summary["awb"] = awb_summary
  camera_summaries = summary.setdefault("cameras", {})

  for cam in cams:
    samples = parse_awb_samples(log_text, cam)
    cam_summary = summarize_awb_samples(samples)
    cam_summary["camera_num"] = CAMERA_NUMS[cam]
    awb_summary["cameras"][cam] = cam_summary
    camera_summaries.setdefault(cam, {})["awb"] = cam_summary
    if samples:
      report.pass_(f"{cam}: {len(samples)} OS04 AWB samples parsed")


def validate_vipc_stats(run_dir: Path, cams: list[str], args: argparse.Namespace, report: Report, summary: dict) -> None:
  data = load_json(run_dir / VIPC_STATS_FILE, report, "VIPC stats", "transport")
  if data is None:
    return
  summary["vipc_stats_path"] = str(run_dir / VIPC_STATS_FILE)

  for cam in cams:
    stats = data.get(cam)
    if not isinstance(stats, dict):
      report.fail(f"{cam}: missing VIPC stats entry", "transport")
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
      report.fail(f"{cam}: VIPC size {width}x{height} below {args.min_width}x{args.min_height}", "transport")
    else:
      report.pass_(f"{cam}: VIPC size {width}x{height}")
    if stride < width:
      report.fail(f"{cam}: VIPC stride {stride} below width {width}", "transport")
    else:
      report.pass_(f"{cam}: VIPC stride {stride} >= width {width}")


def validate_cpu_stats(run_dir: Path, args: argparse.Namespace, report: Report, summary: dict) -> None:
  path = run_dir / CPU_STATS_FILE
  if not path.exists():
    if args.require_cpu_stats:
      report.fail(f"camerad CPU stats missing: {path}", "cpu")
    return

  data = load_json(path, report, "camerad CPU stats", "cpu")
  if data is None:
    return

  if not bool(data.get("available", False)):
    summary["cpu"] = {
      "path": str(path),
      "available": False,
    }
    report.fail("camerad CPU stats unavailable", "cpu")
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
    report.fail(f"camerad CPU sample {wall_seconds:.3f}s below {args.min_cpu_sample_seconds:.3f}s", "cpu")
  else:
    report.pass_(f"camerad CPU sample {wall_seconds:.3f}s >= {args.min_cpu_sample_seconds:.3f}s")

  if cpu_seconds < 0.0:
    report.fail(f"camerad CPU seconds invalid: {cpu_seconds:.3f}", "cpu")
  else:
    report.pass_(f"camerad CPU seconds {cpu_seconds:.3f}")

  if cpu_pct > args.max_camerad_cpu_pct:
    report.fail(f"camerad CPU {cpu_pct:.2f}% > {args.max_camerad_cpu_pct:.2f}%", "cpu")
  else:
    report.pass_(f"camerad CPU {cpu_pct:.2f}% <= {args.max_camerad_cpu_pct:.2f}%")


def validate_image_stats(run_dir: Path, cams: list[str], args: argparse.Namespace, report: Report, summary: dict) -> None:
  if args.no_raw_stats:
    return

  for cam in cams:
    data = load_json(run_dir / STAT_FILES[cam], report, f"{cam} raw stats", "image")
    if data is None:
      continue

    width = int(data.get("width", 0))
    height = int(data.get("height", 0))
    if width < args.min_width or height < args.min_height:
      report.fail(f"{cam}: raw stats size {width}x{height} below {args.min_width}x{args.min_height}", "image")
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
      "tile_luma_clip_hi_count_gt_10pct": int(data.get("tile_luma_clip_hi_count_gt_10pct", -1)),
      "tile_luma_clip_hi_count_gt_50pct": int(data.get("tile_luma_clip_hi_count_gt_50pct", -1)),
      "tile_luma_clip_hi_area_frac_gt_10pct": float(data.get("tile_luma_clip_hi_area_frac_gt_10pct", -1.0)),
      "tile_luma_clip_hi_area_frac_gt_50pct": float(data.get("tile_luma_clip_hi_area_frac_gt_50pct", -1.0)),
      "checks": {},
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
      ("tile_luma_clip_hi_area_frac_gt_10pct", image_summary["tile_luma_clip_hi_area_frac_gt_10pct"], 0.0, args.max_tile_luma_clip_hi_area_frac_gt_10pct),
      ("tile_luma_clip_hi_area_frac_gt_50pct", image_summary["tile_luma_clip_hi_area_frac_gt_50pct"], 0.0, args.max_tile_luma_clip_hi_area_frac_gt_50pct),
      ("uv_hf_abs_mean", image_summary["uv_hf_abs_mean"], 0.0, args.max_uv_hf_abs_mean),
    ]
    for name, value, low, high in checks:
      passed = low <= value <= high
      image_summary["checks"][name] = {
        "value": value,
        "low": low,
        "high": high,
        "passed": passed,
      }
      if value < low or value > high:
        report.fail(f"{cam}: {name}={value:.3f} outside [{low:.3f}, {high:.3f}]", "image")
      else:
        report.pass_(f"{cam}: {name}={value:.3f} inside [{low:.3f}, {high:.3f}]")


def validate_latest_raw_match(run_dir: Path, cams: list[str], args: argparse.Namespace, report: Report, summary: dict) -> None:
  artifact_summary = {
    "require_latest_raw_match": bool(args.require_latest_raw_match),
    "cameras": {},
  }
  summary["artifacts"] = artifact_summary

  if not args.require_latest_raw_match:
    return

  camera_summaries = summary.setdefault("cameras", {})
  for cam in cams:
    latest_path = run_dir / IMAGE_FILES[cam]
    raw_path = run_dir / RAW_IMAGE_FILES[cam]
    cam_summary = {
      "latest_path": str(latest_path),
      "raw_path": str(raw_path),
      "latest_exists": latest_path.exists(),
      "raw_exists": raw_path.exists(),
      "latest_raw_match": False,
    }
    artifact_summary["cameras"][cam] = cam_summary
    camera_summaries.setdefault(cam, {})["artifacts"] = cam_summary

    if not latest_path.exists():
      report.fail(f"{cam}: latest image missing for raw-match check: {latest_path}", "artifact")
      continue
    if not raw_path.exists():
      report.fail(f"{cam}: raw image missing for raw-match check: {raw_path}", "artifact")
      continue

    latest_size = latest_path.stat().st_size
    raw_size = raw_path.stat().st_size
    latest_sha = file_sha256(latest_path)
    raw_sha = file_sha256(raw_path)
    matches = latest_size == raw_size and latest_sha == raw_sha
    cam_summary.update({
      "latest_bytes": latest_size,
      "raw_bytes": raw_size,
      "latest_sha256": latest_sha,
      "raw_sha256": raw_sha,
      "latest_raw_match": matches,
    })
    if matches:
      report.pass_(f"{cam}: latest JPEG matches raw VFE JPEG")
    else:
      report.fail(
        f"{cam}: latest JPEG differs from raw VFE JPEG "
        f"(latest {latest_size} bytes {latest_sha[:12]}, raw {raw_size} bytes {raw_sha[:12]})",
        "artifact",
      )


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
    report.fail(f"dmesg log missing: {path}", "dmesg")
    return

  dmesg_text = path.read_text(errors="replace")
  matches = forbidden_dmesg_matches(dmesg_text)
  dmesg_summary["forbidden_matches"] = matches
  dmesg_summary["line_count"] = len(dmesg_text.splitlines())

  if len(matches) > args.max_dmesg_matches:
    report.fail(f"dmesg forbidden matches {len(matches)} > {args.max_dmesg_matches}", "dmesg")
    for match in matches[:5]:
      report.fail(f"dmesg {match['kind']}: {match['line']}", "dmesg")
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
    help="bench keeps broad bring-up thresholds; road/daylight profiles add stricter image-quality thresholds",
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
  parser.add_argument("--max-tile-luma-clip-hi-area-frac-gt-10pct", type=float, default=999.0, help="maximum fraction of tiles with >10%% RGB-luma clipping")
  parser.add_argument("--max-tile-luma-clip-hi-area-frac-gt-50pct", type=float, default=999.0, help="maximum fraction of tiles with >50%% RGB-luma clipping")
  parser.add_argument("--max-uv-hf-abs-mean", type=float, default=999.0, help="maximum mean high-frequency U/V absolute delta")
  parser.add_argument("--require-latest-raw-match", action="store_true", help="fail unless latest JPEGs exactly match the unenhanced raw VFE JPEGs")
  parser.add_argument("--require-ae-rgb-clip-guard", action="store_true", help="fail unless OS04 AE logs show the RGB clipping guard actively capped EV")
  parser.add_argument("--min-ae-samples", type=int, default=3, help="minimum OS04 AE log samples per selected camera when --require-ae-rgb-clip-guard is set")
  parser.add_argument("--min-ae-rgb-clip", type=float, default=0.079, help="minimum AE rgb_clip fraction considered a guard-triggering highlight")
  parser.add_argument("--min-ae-ev-cap", type=float, default=0.05, help="minimum unclipped_ev - desired_ev considered an active AE RGB clip cap")
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
      "max_tile_luma_clip_hi_area_frac_gt_10pct": args.max_tile_luma_clip_hi_area_frac_gt_10pct,
      "max_tile_luma_clip_hi_area_frac_gt_50pct": args.max_tile_luma_clip_hi_area_frac_gt_50pct,
      "max_uv_hf_abs_mean": args.max_uv_hf_abs_mean,
      "require_latest_raw_match": args.require_latest_raw_match,
      "require_ae_rgb_clip_guard": args.require_ae_rgb_clip_guard,
      "min_ae_samples": args.min_ae_samples,
      "min_ae_rgb_clip": args.min_ae_rgb_clip,
      "min_ae_ev_cap": args.min_ae_ev_cap,
      "check_dmesg": args.check_dmesg,
      "max_dmesg_matches": args.max_dmesg_matches,
    },
  }
  if not args.run_dir.is_dir():
    report.fail(f"run directory missing: {args.run_dir}", "general")
    return 1

  log_text = validate_log(args.run_dir, cams, args, report, summary)
  summarize_vfe_setup_log(log_text, cams, report, summary)
  validate_ae_rgb_clip_guard(log_text, cams, args, report, summary)
  summarize_awb_log(log_text, cams, report, summary)
  validate_vipc_stats(args.run_dir, cams, args, report, summary)
  validate_cpu_stats(args.run_dir, args, report, summary)
  validate_image_stats(args.run_dir, cams, args, report, summary)
  validate_latest_raw_match(args.run_dir, cams, args, report, summary)
  validate_dmesg(args.run_dir, args, report, summary)

  summary["passed"] = not report.failures
  categories = ("transport", "cpu", "dmesg", "image", "artifact", "ae", "general")
  summary["category_passed"] = {
    category: not any(detail["category"] == category for detail in report.failure_details)
    for category in categories
  }
  summary["hardware_path_passed"] = (
    summary["category_passed"]["transport"] and
    summary["category_passed"]["cpu"] and
    summary["category_passed"]["dmesg"] and
    summary["category_passed"]["artifact"] and
    summary["category_passed"]["general"]
  )
  summary["image_quality_passed"] = summary["category_passed"]["image"]
  summary["failures"] = report.failures
  summary["failure_details"] = report.failure_details
  summary["warnings"] = report.warnings
  summary_path = args.summary_json or (args.run_dir / SUMMARY_FILE)
  summary_path.parent.mkdir(parents=True, exist_ok=True)
  summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
  print(f"summary_json: {summary_path}")
  print(f"summary: failures={len(report.failures)} warnings={len(report.warnings)}")
  return 1 if report.failures else 0


if __name__ == "__main__":
  raise SystemExit(main())
