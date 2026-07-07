#!/usr/bin/env python3
"""Capture stable CAM2/CAM3 JPEGs from Dragon camerad VisionIPC."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import statistics
import subprocess
import sys
import textwrap
import time
from pathlib import Path


NCM_IP = "192.168.42.2"
SSH_OPTS = [
  "-i", os.path.expanduser("~/.ssh/comma_setup"),
  "-o", "StrictHostKeyChecking=no",
  "-o", "UserKnownHostsFile=/dev/null",
  "-o", "GlobalKnownHostsFile=/dev/null",
  "-o", "LogLevel=ERROR",
]

REMOTE_IMAGES = {
  "cam1": "/tmp/asius-cam1-latest.jpg",
  "cam2": "/tmp/asius-cam2-latest.jpg",
  "cam3": "/tmp/asius-cam3-latest.jpg",
}
REMOTE_RAW_IMAGES = {
  "cam1": "/tmp/asius-cam1-raw.jpg",
  "cam2": "/tmp/asius-cam2-raw.jpg",
  "cam3": "/tmp/asius-cam3-raw.jpg",
}
REMOTE_RAW_STATS = {
  "cam1": "/tmp/asius-cam1-raw-stats.json",
  "cam2": "/tmp/asius-cam2-raw-stats.json",
  "cam3": "/tmp/asius-cam3-raw-stats.json",
}
LOCAL_IMAGES = {
  "cam1": "latest-camerad-driver.jpg",
  "cam2": "latest-camerad-road.jpg",
  "cam3": "latest-camerad-wide.jpg",
}
LOCAL_RAW_IMAGES = {
  "cam1": "latest-camerad-driver-raw.jpg",
  "cam2": "latest-camerad-road-raw.jpg",
  "cam3": "latest-camerad-wide-raw.jpg",
}
LOCAL_RAW_STATS = {
  "cam1": "latest-camerad-driver-raw-stats.json",
  "cam2": "latest-camerad-road-raw-stats.json",
  "cam3": "latest-camerad-wide-raw-stats.json",
}
HOST_IMAGES = {
  "cam1": Path("/tmp/asius-cam1-latest.jpg"),
  "cam2": Path("/tmp/asius-cam2-latest.jpg"),
  "cam3": Path("/tmp/asius-cam3-latest.jpg"),
}
HOST_RAW_IMAGES = {
  "cam1": Path("/tmp/asius-cam1-raw.jpg"),
  "cam2": Path("/tmp/asius-cam2-raw.jpg"),
  "cam3": Path("/tmp/asius-cam3-raw.jpg"),
}
HOST_MONTAGE = Path("/tmp/asius-cams-latest.jpg")
REMOTE_LOG = "/tmp/camerad_dual_latest.log"
LOCAL_LOG = "latest-camerad-dual.log"
REMOTE_VIPC_STATS = "/tmp/camerad_vipc_stats_latest.json"
LOCAL_VIPC_STATS = "latest-camerad-vipc-stats.json"
REMOTE_CPU_STATS = "/tmp/camerad_cpu_stats_latest.json"
LOCAL_CPU_STATS = "latest-camerad-cpu-stats.json"
REMOTE_DMESG_LOG = "/tmp/camerad_dmesg_latest.log"
LOCAL_DMESG_LOG = "latest-camerad-dmesg.log"


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
  return subprocess.run(cmd, check=True, **kwargs)


def camera_list(selection: str) -> list[str]:
  if selection == "all":
    return ["cam1", "cam2", "cam3"]
  if selection == "both":
    return ["cam2", "cam3"]
  return [selection]


def remote_env(selection: str, exposure_lines: int, target_grey: float,
               preview_saturation: float, preview_median: float, pix_ioctl: bool,
               debug_frames: bool, extra_env: list[str]) -> list[str]:
  selected = camera_list(selection)
  env = [
    "ASIUS=1",
    "ASIUS_CAMERA_ONE=1",
    "LOGPRINT=debug",
    f"ASIUS_CAM_TARGET_GREY={target_grey}",
    f"ASIUS_CAM_START_EXPOSURE_LINES={exposure_lines}",
    f"ASIUS_SNAPSHOT_SATURATION={preview_saturation}",
    f"ASIUS_SNAPSHOT_TARGET_MEDIAN={preview_median}",
  ]
  if debug_frames:
    env.append("DEBUG_FRAMES=1")
  if pix_ioctl:
    env.append("ASIUS_CAM_PIX_IOCTL=1")
  if "cam1" not in selected:
    env.append("DISABLE_DRIVER=1")
  if "cam2" not in selected:
    env.append("DISABLE_ROAD=1")
  if "cam3" not in selected:
    env.append("DISABLE_WIDE_ROAD=1")
  env.extend(extra_env)
  return env


def remote_script(openpilot_dir: str, selection: str, settle: float, exposure_lines: int, target_grey: float,
                  preview_saturation: float, preview_median: float,
                  pix_ioctl: bool, raw_debug: bool, monitor_duration: float,
                  debug_frames: bool, capture_dmesg: bool, extra_env: list[str]) -> str:
  cameras = camera_list(selection)
  targets_literal = repr(cameras)
  raw_images_literal = repr(REMOTE_RAW_IMAGES)
  raw_stats_literal = repr(REMOTE_RAW_STATS)
  env_words = " ".join(shlex.quote(word) for word in remote_env(
    selection, exposure_lines, target_grey, preview_saturation, preview_median, pix_ioctl, debug_frames, extra_env))
  openpilot_dir_q = shlex.quote(openpilot_dir)
  capture_dmesg_value = "1" if capture_dmesg else "0"
  return textwrap.dedent(f"""\
    set -e
    lock_dir=/tmp/camerad_capture_latest.lock
    if ! mkdir "$lock_dir" 2>/dev/null; then
      echo "another camerad_capture_latest.py run is active on this Dragon" >&2
      exit 75
    fi
    pid=""
    capture_dmesg={capture_dmesg_value}
    dmesg_before_lines=0
    collect_dmesg() {{
      [ "$capture_dmesg" = "1" ] || return 0
      start_line=$((dmesg_before_lines + 1))
      if command -v dmesg >/dev/null 2>&1; then
        dmesg 2>/dev/null | tail -n +"$start_line" > {REMOTE_DMESG_LOG} || true
      else
        echo "dmesg command unavailable" > {REMOTE_DMESG_LOG}
      fi
    }}
    cleanup() {{
      if [ -n "$pid" ]; then
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
      fi
      collect_dmesg
      rmdir "$lock_dir" 2>/dev/null || true
    }}
    trap cleanup EXIT
    cd {openpilot_dir_q}
    rm -f /tmp/asius-cam1-latest.jpg /tmp/asius-cam2-latest.jpg /tmp/asius-cam3-latest.jpg \\
      /tmp/asius-cam1-raw.jpg /tmp/asius-cam2-raw.jpg /tmp/asius-cam3-raw.jpg \\
      /tmp/asius-cam1-raw-stats.json /tmp/asius-cam2-raw-stats.json /tmp/asius-cam3-raw-stats.json \\
      {REMOTE_LOG} {REMOTE_VIPC_STATS} {REMOTE_CPU_STATS} {REMOTE_DMESG_LOG} /tmp/camerad_dual_latest.pid
    pkill -x camerad 2>/dev/null || true
    pkill -f '/tmp/camerad-cache' 2>/dev/null || true
    if [ "$capture_dmesg" = "1" ] && command -v dmesg >/dev/null 2>&1; then
      dmesg_before_lines=$(dmesg 2>/dev/null | wc -l || echo 0)
    fi
    export {env_words}
    ./system/camerad/camerad > {REMOTE_LOG} 2>&1 &
    pid=$!
    echo "$pid" > /tmp/camerad_dual_latest.pid
    sleep 1
    sleep {settle:.3f}
    /usr/local/venv/bin/python - <<'PY'
    import json
    import os
    import time

    with open("/tmp/camerad_dual_latest.pid") as f:
      pid = int(f.read().strip())
    clk_tck = os.sysconf("SC_CLK_TCK")
    monitor_duration = {monitor_duration:.6f}

    def proc_ticks():
      try:
        with open("/proc/%d/stat" % pid) as f:
          fields = f.read().split()
        return int(fields[13]) + int(fields[14])
      except (FileNotFoundError, IndexError, ValueError):
        return None

    start_wall = time.monotonic()
    start_ticks = proc_ticks()
    time.sleep(monitor_duration)
    end_wall = time.monotonic()
    end_ticks = proc_ticks()
    wall_seconds = max(end_wall - start_wall, 0.0)
    cpu_seconds = None
    cpu_pct = None
    available = start_ticks is not None and end_ticks is not None and wall_seconds > 0.0
    if available:
      cpu_seconds = max(end_ticks - start_ticks, 0) / float(clk_tck)
      cpu_pct = 100.0 * cpu_seconds / wall_seconds

    stats = dict(
      pid=pid,
      available=available,
      wall_seconds=wall_seconds,
      cpu_seconds=cpu_seconds,
      single_core_cpu_pct=cpu_pct,
      clk_tck=clk_tck,
      start_ticks=start_ticks,
      end_ticks=end_ticks,
    )
    with open("{REMOTE_CPU_STATS}", "w") as f:
      json.dump(stats, f, indent=2, sort_keys=True)
    PY
    /usr/local/venv/bin/python - <<'PY'
    import json
    import time
    import numpy as np
    from PIL import Image
    from msgq.visionipc import VisionIpcClient, VisionStreamType
    from openpilot.system.camerad.snapshot import extract_image, jpeg_write

    selected = {targets_literal}
    raw_debug = {raw_debug!r}
    raw_images = {raw_images_literal}
    raw_stats = {raw_stats_literal}
    streams = {{
      "cam1": ("driver", VisionStreamType.VISION_STREAM_DRIVER, "/tmp/asius-cam1-latest.jpg"),
      "cam2": ("road", VisionStreamType.VISION_STREAM_ROAD, "/tmp/asius-cam2-latest.jpg"),
      "cam3": ("wide", VisionStreamType.VISION_STREAM_WIDE_ROAD, "/tmp/asius-cam3-latest.jpg"),
    }}

    def highfreq_abs_mean(plane):
      if plane.shape[0] < 2 or plane.shape[1] < 2:
        return 0.0
      plane_i = plane.astype(np.int16)
      dx = np.abs(np.diff(plane_i, axis=1)).mean()
      dy = np.abs(np.diff(plane_i, axis=0)).mean()
      return float((dx + dy) / 2.0)

    def tile_frame_stats(y, u, v, rgb, luma, rows=5, cols=5):
      tiles = []
      for row in range(rows):
        y0 = rgb.shape[0] * row // rows
        y1 = rgb.shape[0] * (row + 1) // rows
        for col in range(cols):
          x0 = rgb.shape[1] * col // cols
          x1 = rgb.shape[1] * (col + 1) // cols
          uy0 = max(0, y0 // 2)
          uy1 = max(uy0 + 1, y1 // 2)
          ux0 = max(0, x0 // 2)
          ux1 = max(ux0 + 1, x1 // 2)

          rgb_tile = rgb[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
          y_tile = y[y0:y1, x0:x1]
          luma_tile = luma[y0:y1, x0:x1]
          rgb_median = np.median(rgb_tile, axis=0)
          tile = dict(
            row=int(row),
            col=int(col),
            x0=int(x0),
            y0=int(y0),
            x1=int(x1),
            y1=int(y1),
            y_median=float(np.median(y_tile)),
            y_clip_hi_frac=float((y_tile >= 250).mean()),
            luma_clip_hi_frac=float((luma_tile >= 250.0).mean()),
            u_median=float(np.median(u[uy0:uy1, ux0:ux1])),
            v_median=float(np.median(v[uy0:uy1, ux0:ux1])),
            rgb_median=[float(x) for x in rgb_median],
            rgb_median_spread=float(rgb_median.max() - rgb_median.min()),
          )
          tile["uv_median_offset"] = float(max(abs(tile["u_median"] - 128.0), abs(tile["v_median"] - 128.0)))
          tiles.append(tile)

      y_medians = np.array([tile["y_median"] for tile in tiles], dtype=np.float32)
      u_medians = np.array([tile["u_median"] for tile in tiles], dtype=np.float32)
      v_medians = np.array([tile["v_median"] for tile in tiles], dtype=np.float32)
      rgb_spreads = np.array([tile["rgb_median_spread"] for tile in tiles], dtype=np.float32)
      uv_offsets = np.array([tile["uv_median_offset"] for tile in tiles], dtype=np.float32)
      y_clip_hi_fracs = np.array([tile["y_clip_hi_frac"] for tile in tiles], dtype=np.float32)
      luma_clip_hi_fracs = np.array([tile["luma_clip_hi_frac"] for tile in tiles], dtype=np.float32)
      tile_count = len(tiles)
      return dict(
        tile_rows=int(rows),
        tile_cols=int(cols),
        tiles=tiles,
        tile_y_median_range=float(y_medians.max() - y_medians.min()),
        tile_u_median_range=float(u_medians.max() - u_medians.min()),
        tile_v_median_range=float(v_medians.max() - v_medians.min()),
        tile_uv_median_range=float(max(u_medians.max() - u_medians.min(), v_medians.max() - v_medians.min())),
        tile_max_uv_median_offset=float(uv_offsets.max()),
        tile_p95_uv_median_offset=float(np.percentile(uv_offsets, 95.0)),
        tile_max_rgb_median_spread=float(rgb_spreads.max()),
        tile_p95_rgb_median_spread=float(np.percentile(rgb_spreads, 95.0)),
        tile_max_y_clip_hi_frac=float(y_clip_hi_fracs.max()),
        tile_p95_y_clip_hi_frac=float(np.percentile(y_clip_hi_fracs, 95.0)),
        tile_max_luma_clip_hi_frac=float(luma_clip_hi_fracs.max()),
        tile_p95_luma_clip_hi_frac=float(np.percentile(luma_clip_hi_fracs, 95.0)),
        tile_luma_clip_hi_count_gt_10pct=int((luma_clip_hi_fracs > 0.10).sum()),
        tile_luma_clip_hi_count_gt_50pct=int((luma_clip_hi_fracs > 0.50).sum()),
        tile_luma_clip_hi_area_frac_gt_10pct=float((luma_clip_hi_fracs > 0.10).sum() / tile_count),
        tile_luma_clip_hi_area_frac_gt_50pct=float((luma_clip_hi_fracs > 0.50).sum() / tile_count),
      )

    def frame_stats(buf, rgb):
      y = np.array(buf.data[:buf.uv_offset], dtype=np.uint8).reshape((-1, buf.stride))[:buf.height, :buf.width]
      uv_height = ((buf.height // 2) + 15) // 16 * 16
      uv_plane_size = buf.stride * uv_height
      uv_data = buf.data[buf.uv_offset:buf.uv_offset + uv_plane_size]
      u = np.array(uv_data[::2], dtype=np.uint8).reshape((-1, buf.stride // 2))[:buf.height // 2, :buf.width // 2]
      v = np.array(uv_data[1::2], dtype=np.uint8).reshape((-1, buf.stride // 2))[:buf.height // 2, :buf.width // 2]
      luma = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
      chroma = np.sqrt(((rgb.astype(np.float32) - rgb.mean(axis=2, keepdims=True)) ** 2).mean(axis=2))
      center = y[buf.height // 4:buf.height * 3 // 4, buf.width // 4:buf.width * 3 // 4]
      u_center = u[buf.height // 8:buf.height * 3 // 8, buf.width // 8:buf.width * 3 // 8]
      v_center = v[buf.height // 8:buf.height * 3 // 8, buf.width // 8:buf.width * 3 // 8]
      flat_rgb = rgb.reshape(-1, 3).astype(np.float32)
      rgb_mean = flat_rgb.mean(axis=0)
      rgb_median = np.median(flat_rgb, axis=0)
      stats = {{
        "width": int(buf.width),
        "height": int(buf.height),
        "stride": int(buf.stride),
        "uv_offset": int(buf.uv_offset),
        "y_mean": float(y.mean()),
        "y_median": float(np.median(y)),
        "y_center_median": float(np.median(center)),
        "y_p01": float(np.percentile(y, 1.0)),
        "y_p99": float(np.percentile(y, 99.0)),
        "y_clip_lo_frac": float((y <= 4).mean()),
        "y_clip_hi_frac": float((y >= 250).mean()),
        "u_mean": float(u.mean()),
        "u_median": float(np.median(u)),
        "u_center_median": float(np.median(u_center)),
        "u_abs_mean": float(np.abs(u.astype(np.int16) - 128).mean()),
        "v_mean": float(v.mean()),
        "v_median": float(np.median(v)),
        "v_center_median": float(np.median(v_center)),
        "v_abs_mean": float(np.abs(v.astype(np.int16) - 128).mean()),
        "uv_abs_mean": float((np.abs(u.astype(np.int16) - 128).mean() + np.abs(v.astype(np.int16) - 128).mean()) / 2.0),
        "uv_center_abs_mean": float((np.abs(u_center.astype(np.int16) - 128).mean() + np.abs(v_center.astype(np.int16) - 128).mean()) / 2.0),
        "rgb_mean": [float(x) for x in rgb_mean],
        "rgb_median": [float(x) for x in rgb_median],
        "rgb_mean_spread": float(rgb_mean.max() - rgb_mean.min()),
        "rgb_median_spread": float(rgb_median.max() - rgb_median.min()),
        "rgb_median_rg_delta": float(rgb_median[0] - rgb_median[1]),
        "rgb_median_bg_delta": float(rgb_median[2] - rgb_median[1]),
        "luma_median": float(np.median(luma)),
        "luma_clip_hi_frac": float((luma >= 250.0).mean()),
        "mean_chroma": float(chroma.mean()),
        "y_hf_abs_mean": highfreq_abs_mean(y),
        "u_hf_abs_mean": highfreq_abs_mean(u),
        "v_hf_abs_mean": highfreq_abs_mean(v),
      }}
      stats["uv_hf_abs_mean"] = float((stats["u_hf_abs_mean"] + stats["v_hf_abs_mean"]) / 2.0)
      stats.update(tile_frame_stats(y, u, v, rgb, luma))
      return stats

    clients = {{}}
    unavailable = set()
    deadline = time.monotonic() + 10.0
    while len(clients) + len(unavailable) < len(selected) and time.monotonic() < deadline:
      for key in selected:
        if key in clients or key in unavailable:
          continue
        label, stream, out = streams[key]
        c = VisionIpcClient("camerad", stream, True)
        if c.connect(False):
          if c.width is None or c.height is None or c.stride is None:
            unavailable.add(key)
            print(f"connected {{key}} {{label}} without buffers")
          else:
            clients[key] = (label, c, out)
            print(f"connected {{key}} {{label}} {{c.width}}x{{c.height}} stride={{c.stride}}")
      if len(clients) + len(unavailable) < len(selected):
        time.sleep(0.1)

    missing = [key for key in selected if key not in clients]
    if missing:
      print("missing streams: " + ",".join(missing))

    saved = {{}}
    capture_stats = {{}}
    frame_deadline = time.monotonic() + 10.0
    while len(saved) < len(clients) and time.monotonic() < frame_deadline:
      progressed = False
      for key in selected:
        if key not in clients or key in saved:
          continue
        label, client, out = clients[key]
        buf = client.recv(100)
        if buf is None:
          continue

        frame_id = int(client.frame_id)
        img = extract_image(buf)
        if raw_debug:
          Image.fromarray(img).save(raw_images[key], "JPEG", quality=95)
          raw_frame_stats = frame_stats(buf, img)
          raw_frame_stats["frame_id"] = frame_id
          with open(raw_stats[key], "w") as f:
            json.dump(raw_frame_stats, f, indent=2, sort_keys=True)
        jpeg_write(out, img)
        saved[key] = True
        capture_stats[key] = {{
          "saved_frame_id": frame_id,
          "width": int(buf.width),
          "height": int(buf.height),
          "stride": int(buf.stride),
        }}
        progressed = True
        print(f"saved {{key}} {{label}} {{out}} shape={{img.shape}} frame_id={{frame_id}}")
      if not progressed:
        time.sleep(0.02)

    with open("{REMOTE_VIPC_STATS}", "w") as f:
      json.dump(capture_stats, f, indent=2, sort_keys=True)

    for key in selected:
      if key in clients and key not in saved:
        print(f"no frame from {{key}} {{clients[key][0]}}")
    if not saved:
      raise RuntimeError("no selected camera produced a frame")
    PY
  """)


def pull_file(remote: str, local: Path) -> None:
  local.parent.mkdir(parents=True, exist_ok=True)
  cmd = ["scp", *SSH_OPTS, f"comma@{NCM_IP}:{remote}", str(local)]
  for attempt in range(1, 4):
    try:
      run(cmd)
      return
    except subprocess.CalledProcessError:
      if attempt == 3:
        raise
      time.sleep(1.0)


def summarize_fps(log_path: Path) -> None:
  if not log_path.exists():
    return
  by_cam: dict[str, list[float]] = {}
  for line in log_path.read_text(errors="replace").splitlines():
    match = re.search(r"cam (\d+) frame \d+ .* ts ([0-9.]+) ms", line)
    if match:
      by_cam.setdefault(match.group(1), []).append(float(match.group(2)))

  labels = {"0": "cam3/wide", "1": "cam2/road", "2": "cam1/driver"}
  for cam, times in sorted(by_cam.items()):
    if len(times) < 2:
      continue
    intervals = [b - a for a, b in zip(times, times[1:])]
    median_ms = statistics.median(intervals)
    slow_gaps = sum(1 for interval in intervals if interval > 75.0)
    print(f"{labels.get(cam, 'cam ' + cam)}: {len(times)} frames, median {1000.0 / median_ms:.2f} FPS, slow_gaps={slow_gaps}")


def refresh_host_images(cameras: list[str], out_dir: Path) -> None:
  from PIL import Image, ImageDraw

  labels = {
    "cam1": "CAM1 driver",
    "cam2": "CAM2 road",
    "cam3": "CAM3 wide",
  }
  images: list[tuple[str, Image.Image]] = []
  for cam in cameras:
    local = out_dir / LOCAL_IMAGES[cam]
    host = HOST_IMAGES[cam]
    host.write_bytes(local.read_bytes())
    images.append((labels[cam], Image.open(local).convert("RGB")))

  width = max(image.width for _, image in images)
  height = sum(image.height + 40 for _, image in images)
  montage = Image.new("RGB", (width, height), "black")
  draw = ImageDraw.Draw(montage)
  y = 0
  for label, image in images:
    draw.text((8, y + 8), label, fill=(255, 255, 255))
    y += 40
    montage.paste(image, (0, y))
    y += image.height
  montage.save(HOST_MONTAGE, "JPEG", quality=95)


def refresh_host_raw_images(cameras: list[str], out_dir: Path) -> None:
  for cam in cameras:
    local = out_dir / LOCAL_RAW_IMAGES[cam]
    if local.exists():
      HOST_RAW_IMAGES[cam].write_bytes(local.read_bytes())


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--cam", choices=("cam1", "cam2", "cam3", "both", "all"), default="both")
  parser.add_argument("--out-dir", default="/tmp/dragon_os04_bench")
  parser.add_argument("--openpilot-dir", default="/data/openpilot", help="remote openpilot checkout to run camerad from")
  parser.add_argument("--settle", type=float, default=7.0, help="seconds to let AE settle before saving")
  parser.add_argument("--exposure-lines", type=int, default=600, help="initial OS04 exposure lines")
  parser.add_argument("--target-grey", type=float, default=0.48, help="OS04 AE target grey fraction")
  parser.add_argument("--chroma-scale", type=float, default=2.05, help="legacy ignored option; One camerad no longer has a software debayer path on this branch")
  parser.add_argument("--preview-saturation", type=float, default=1.00, help="JPEG preview saturation boost")
  parser.add_argument("--preview-median", type=float, default=115.0, help="JPEG preview target median luma")
  parser.add_argument("--pix-ioctl", action="store_true", help="use the experimental liberation-day-style VFE userspace ioctl path")
  parser.add_argument("--rdi", action="store_true", help="legacy unsupported option; use tools/camss_rdi_probe.c for raw RDI probing")
  parser.add_argument("--raw-debug", action="store_true", help="save unenhanced NV12-derived JPEGs and JSON stats beside the normal preview JPEG")
  parser.add_argument("--monitor-duration", type=float, default=5.0, help="seconds to keep camerad running before the one-shot JPEG capture")
  parser.add_argument("--camerad-debug-frames", action="store_true", help="make camerad print one log line per dequeued frame")
  parser.add_argument("--validate-vfe", action="store_true", help="run the local VFE PIX/DMABUF validator after capture; implies --raw-debug and --camerad-debug-frames")
  parser.add_argument("--validate-min-frames", type=int, default=20, help="minimum VFE debug frames per selected camera when --validate-vfe is set")
  parser.add_argument("--validate-min-fps", type=float, default=18.0, help="minimum median VFE FPS when --validate-vfe is set")
  parser.add_argument("--validate-max-slow-gaps", type=int, default=0, help="maximum slow VFE frame gaps when --validate-vfe is set")
  parser.add_argument("--validate-max-cpu-pct", type=float, default=10.0, help="maximum camerad single-core CPU percent during the monitor window when --validate-vfe is set")
  parser.add_argument("--validate-quality-profile", choices=("bench", "road", "road-spatial"), default="bench", help="image-stat threshold profile passed to validate_camerad_vfe.py")
  parser.add_argument("--check-dmesg", action="store_true", help="capture dmesg during the camerad run; with --validate-vfe, fail on CAMSS/VFE errors, stalls, or buffer-address spam")
  parser.add_argument("--validate-max-dmesg-matches", type=int, default=0, help="maximum forbidden dmesg matches allowed when --validate-vfe --check-dmesg is set")
  parser.add_argument("--env", action="append", default=[], metavar="NAME=VALUE", help="extra remote camerad environment variable")
  args = parser.parse_args()

  if args.rdi:
    parser.error("--rdi was removed from One camerad on the hardware VFE branch; use tools/camss_rdi_probe.c for raw RDI probing")

  if args.validate_vfe:
    args.raw_debug = True
    args.camerad_debug_frames = True

  out_dir = Path(args.out_dir)
  script = remote_script(args.openpilot_dir, args.cam, args.settle, args.exposure_lines, args.target_grey,
                         args.preview_saturation, args.preview_median,
                         args.pix_ioctl, args.raw_debug, args.monitor_duration,
                         args.camerad_debug_frames, args.check_dmesg, args.env)
  run(["ssh", *SSH_OPTS, f"comma@{NCM_IP}", "bash", "-s"], input=script, text=True)

  cameras = camera_list(args.cam)
  pulled = []
  for cam in cameras:
    local = out_dir / LOCAL_IMAGES[cam]
    present = subprocess.run(["ssh", *SSH_OPTS, f"comma@{NCM_IP}", "test", "-s", REMOTE_IMAGES[cam]], check=False)
    if present.returncode != 0:
      print(f"{cam}: no remote image at {REMOTE_IMAGES[cam]}")
      continue
    pull_file(REMOTE_IMAGES[cam], local)
    pulled.append(cam)
    print(f"{cam}: remote={REMOTE_IMAGES[cam]} local={local} bytes={local.stat().st_size}")

  if not pulled:
    return 1

  refresh_host_images(pulled, out_dir)
  print(f"host_montage={HOST_MONTAGE} bytes={HOST_MONTAGE.stat().st_size}")

  if args.raw_debug:
    for cam in pulled:
      raw_local = out_dir / LOCAL_RAW_IMAGES[cam]
      raw_stats_local = out_dir / LOCAL_RAW_STATS[cam]
      raw_present = subprocess.run(["ssh", *SSH_OPTS, f"comma@{NCM_IP}", "test", "-s", REMOTE_RAW_IMAGES[cam]], check=False)
      stats_present = subprocess.run(["ssh", *SSH_OPTS, f"comma@{NCM_IP}", "test", "-s", REMOTE_RAW_STATS[cam]], check=False)
      if raw_present.returncode == 0:
        pull_file(REMOTE_RAW_IMAGES[cam], raw_local)
        print(f"{cam}: raw_remote={REMOTE_RAW_IMAGES[cam]} raw_local={raw_local} bytes={raw_local.stat().st_size}")
      if stats_present.returncode == 0:
        pull_file(REMOTE_RAW_STATS[cam], raw_stats_local)
        print(f"{cam}: stats_remote={REMOTE_RAW_STATS[cam]} stats_local={raw_stats_local} bytes={raw_stats_local.stat().st_size}")
    refresh_host_raw_images(pulled, out_dir)

  local_log = out_dir / LOCAL_LOG
  pull_file(REMOTE_LOG, local_log)
  print(f"log: remote={REMOTE_LOG} local={local_log} bytes={local_log.stat().st_size}")
  summarize_fps(local_log)
  stats_local = out_dir / LOCAL_VIPC_STATS
  stats_present = subprocess.run(["ssh", *SSH_OPTS, f"comma@{NCM_IP}", "test", "-s", REMOTE_VIPC_STATS], check=False)
  if stats_present.returncode == 0:
    pull_file(REMOTE_VIPC_STATS, stats_local)
    print(f"vipc_stats: remote={REMOTE_VIPC_STATS} local={stats_local} bytes={stats_local.stat().st_size}")
  cpu_stats_local = out_dir / LOCAL_CPU_STATS
  cpu_stats_present = subprocess.run(["ssh", *SSH_OPTS, f"comma@{NCM_IP}", "test", "-s", REMOTE_CPU_STATS], check=False)
  if cpu_stats_present.returncode == 0:
    pull_file(REMOTE_CPU_STATS, cpu_stats_local)
    try:
      cpu_stats = json.loads(cpu_stats_local.read_text())
      cpu_pct = cpu_stats.get("single_core_cpu_pct")
      cpu_text = "unavailable" if cpu_pct is None else f"{float(cpu_pct):.1f}%"
      print(f"cpu_stats: remote={REMOTE_CPU_STATS} local={cpu_stats_local} camerad_cpu={cpu_text} bytes={cpu_stats_local.stat().st_size}")
    except (OSError, ValueError, TypeError):
      print(f"cpu_stats: remote={REMOTE_CPU_STATS} local={cpu_stats_local} bytes={cpu_stats_local.stat().st_size}")
  if args.check_dmesg:
    dmesg_log_local = out_dir / LOCAL_DMESG_LOG
    dmesg_present = subprocess.run(["ssh", *SSH_OPTS, f"comma@{NCM_IP}", "test", "-e", REMOTE_DMESG_LOG], check=False)
    if dmesg_present.returncode == 0:
      pull_file(REMOTE_DMESG_LOG, dmesg_log_local)
      print(f"dmesg: remote={REMOTE_DMESG_LOG} local={dmesg_log_local} bytes={dmesg_log_local.stat().st_size}")
    else:
      print(f"dmesg: no remote log at {REMOTE_DMESG_LOG}")
  if args.validate_vfe:
    validator = Path(__file__).with_name("validate_camerad_vfe.py")
    validator_cmd = [
      sys.executable,
      str(validator),
      str(out_dir),
      "--cam", args.cam,
      "--quality-profile", args.validate_quality_profile,
      "--min-frames", str(args.validate_min_frames),
      "--min-fps", str(args.validate_min_fps),
      "--max-slow-gaps", str(args.validate_max_slow_gaps),
      "--require-cpu-stats",
      "--max-camerad-cpu-pct", str(args.validate_max_cpu_pct),
    ]
    if args.check_dmesg:
      validator_cmd.extend(["--check-dmesg", "--max-dmesg-matches", str(args.validate_max_dmesg_matches)])
    ret = subprocess.run(validator_cmd)
    if ret.returncode != 0:
      return ret.returncode
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
