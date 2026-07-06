#!/usr/bin/env python3
"""Capture stable CAM2/CAM3 JPEGs from Dragon camerad VisionIPC."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import statistics
import subprocess
import textwrap
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
LOCAL_IMAGES = {
  "cam1": "latest-camerad-driver.jpg",
  "cam2": "latest-camerad-road.jpg",
  "cam3": "latest-camerad-wide.jpg",
}
REMOTE_LOG = "/tmp/camerad_dual_latest.log"
LOCAL_LOG = "latest-camerad-dual.log"


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
  return subprocess.run(cmd, check=True, **kwargs)


def camera_list(selection: str) -> list[str]:
  if selection == "all":
    return ["cam1", "cam2", "cam3"]
  if selection == "both":
    return ["cam2", "cam3"]
  return [selection]


def remote_env(selection: str, exposure_lines: int) -> list[str]:
  selected = camera_list(selection)
  env = [
    "ASIUS=1",
    "LOGPRINT=debug",
    "DEBUG_FRAMES=1",
    f"ASIUS_CAM_START_EXPOSURE_LINES={exposure_lines}",
  ]
  if "cam1" not in selected:
    env.append("DISABLE_DRIVER=1")
  if "cam2" not in selected:
    env.append("DISABLE_ROAD=1")
  if "cam3" not in selected:
    env.append("DISABLE_WIDE_ROAD=1")
  return env


def remote_script(selection: str, settle: float, exposure_lines: int) -> str:
  cameras = camera_list(selection)
  targets_literal = repr(cameras)
  env_words = " ".join(shlex.quote(word) for word in remote_env(selection, exposure_lines))
  return textwrap.dedent(f"""\
    set -e
    cd /data/openpilot
    rm -f /tmp/asius-cam2-latest.jpg /tmp/asius-cam3-latest.jpg {REMOTE_LOG} /tmp/camerad_dual_latest.pid
    pkill -x camerad 2>/dev/null || true
    env {env_words} ./system/camerad/camerad > {REMOTE_LOG} 2>&1 &
    pid=$!
    echo "$pid" > /tmp/camerad_dual_latest.pid
    cleanup() {{
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    }}
    trap cleanup EXIT
    sleep 1
    /usr/local/venv/bin/python - <<'PY'
    import time
    from msgq.visionipc import VisionIpcClient, VisionStreamType
    from openpilot.system.camerad.snapshot import extract_image, jpeg_write

    selected = {targets_literal}
    streams = {{
      "cam1": ("driver", VisionStreamType.VISION_STREAM_DRIVER, "/tmp/asius-cam1-latest.jpg"),
      "cam2": ("road", VisionStreamType.VISION_STREAM_ROAD, "/tmp/asius-cam2-latest.jpg"),
      "cam3": ("wide", VisionStreamType.VISION_STREAM_WIDE_ROAD, "/tmp/asius-cam3-latest.jpg"),
    }}
    clients = {{}}
    deadline = time.monotonic() + 10.0
    while len(clients) < len(selected) and time.monotonic() < deadline:
      for key in selected:
        if key in clients:
          continue
        label, stream, out = streams[key]
        c = VisionIpcClient("camerad", stream, True)
        if c.connect(False):
          clients[key] = (label, c, out)
          print(f"connected {{key}} {{label}} {{c.width}}x{{c.height}} stride={{c.stride}}")
      if len(clients) < len(selected):
        time.sleep(0.1)

    missing = [key for key in selected if key not in clients]
    if missing:
      raise RuntimeError("missing streams: " + ",".join(missing))

    time.sleep({settle:.3f})
    for key, (label, client, out) in clients.items():
      buf = None
      for _ in range(20):
        buf = client.recv(500)
        if buf is not None:
          break
      if buf is None:
        raise RuntimeError(f"no frame from {{key}} {{label}}")
      img = extract_image(buf)
      jpeg_write(out, img)
      print(f"saved {{key}} {{label}} {{out}} shape={{img.shape}} frame_id={{client.frame_id}}")
    PY
  """)


def pull_file(remote: str, local: Path) -> None:
  local.parent.mkdir(parents=True, exist_ok=True)
  run(["scp", *SSH_OPTS, f"comma@{NCM_IP}:{remote}", str(local)])


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


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--cam", choices=("cam1", "cam2", "cam3", "both", "all"), default="both")
  parser.add_argument("--out-dir", default="/tmp/dragon_os04_bench")
  parser.add_argument("--settle", type=float, default=5.0, help="seconds to let AE settle before saving")
  parser.add_argument("--exposure-lines", type=int, default=5, help="initial OS04 exposure lines")
  args = parser.parse_args()

  out_dir = Path(args.out_dir)
  script = remote_script(args.cam, args.settle, args.exposure_lines)
  run(["ssh", *SSH_OPTS, f"comma@{NCM_IP}", "bash", "-s"], input=script, text=True)

  for cam in camera_list(args.cam):
    local = out_dir / LOCAL_IMAGES[cam]
    pull_file(REMOTE_IMAGES[cam], local)
    print(f"{cam}: remote={REMOTE_IMAGES[cam]} local={local} bytes={local.stat().st_size}")

  local_log = out_dir / LOCAL_LOG
  pull_file(REMOTE_LOG, local_log)
  print(f"log: remote={REMOTE_LOG} local={local_log} bytes={local_log.stat().st_size}")
  summarize_fps(local_log)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
