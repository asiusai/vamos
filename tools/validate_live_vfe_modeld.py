#!/usr/bin/env python3
"""Validate live CAM2/CAM3 VFE VisionIPC into modeld on Dragon."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import textwrap
from pathlib import Path


NCM_IP = "192.168.42.2"
SSH_OPTS = [
  "-i", os.path.expanduser("~/.ssh/comma_setup"),
  "-o", "StrictHostKeyChecking=no",
  "-o", "UserKnownHostsFile=/dev/null",
  "-o", "GlobalKnownHostsFile=/dev/null",
  "-o", "LogLevel=ERROR",
  "-o", "BatchMode=yes",
  "-o", "ConnectTimeout=8",
  "-o", "ServerAliveInterval=5",
  "-o", "ServerAliveCountMax=2",
]
DEFAULT_PULL_TIMEOUT = 30.0

REMOTE_CAMERAD_LOG = "/tmp/asius_live_vfe_modeld_camerad.log"
REMOTE_MODELD_LOG = "/tmp/asius_live_vfe_modeld_modeld.log"
REMOTE_DMESG_LOG = "/tmp/asius_live_vfe_modeld_dmesg.log"
REMOTE_SUMMARY = "/tmp/asius_live_vfe_modeld_summary.json"
LOCAL_CAMERAD_LOG = "live-vfe-modeld-camerad.log"
LOCAL_MODELD_LOG = "live-vfe-modeld-modeld.log"
LOCAL_DMESG_LOG = "live-vfe-modeld-dmesg.log"
LOCAL_SUMMARY = "live-vfe-modeld-summary.json"

CAMERA_NUMS = {
  "cam2": "1",
  "cam3": "0",
}

FATAL_CAMERAD_MARKERS = [
  "falling back to RDI",
  "NV12 sw debayer",
  "VFE PIX unavailable",
  "falling back to V4L2 MMAP CPU-copy path",
]

DMESG_FORBIDDEN_PATTERNS = [
  (
    "normal VFE PIX buffer-address spam",
    re.compile(r"\bpix buf\d+ addr0=", re.IGNORECASE),
  ),
  (
    "VFE PIX stall/recovery warning",
    re.compile(r"\bvfe\d+ pix .*\b(stall|recovering)\b", re.IGNORECASE),
  ),
  (
    "camera pipeline error/failure",
    re.compile(
      r"\b(camss|csiphy|csid|vfe|os04c10|cci|camera)\b.*"
      r"\b(error|failed|failure|fault|timeout|timed out)\b",
      re.IGNORECASE,
    ),
  ),
]


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
  print("+", " ".join(shlex.quote(c) for c in cmd), flush=True)
  return subprocess.run(cmd, check=True, **kwargs)


def bash_env_word(word: str) -> str:
  if "\n" not in word and "\r" not in word:
    return shlex.quote(word)

  escaped = []
  for char in word:
    if char == "\\":
      escaped.append("\\\\")
    elif char == "'":
      escaped.append("\\'")
    elif char == "\n":
      escaped.append("\\n")
    elif char == "\r":
      escaped.append("\\r")
    elif char == "\t":
      escaped.append("\\t")
    else:
      escaped.append(char)
  return "$'" + "".join(escaped) + "'"


def remote_env(target_grey: float, extra_env: list[str]) -> list[str]:
  env = [
    "ASIUS=1",
    "ASIUS_CAMERA_ONE=1",
    "LOGPRINT=debug",
    "DEBUG_FRAMES=1",
    "DISABLE_DRIVER=1",
    "VISIONBUF_USE_DMA_HEAP=1",
  ]
  if target_grey > 0.0:
    env.append(f"ASIUS_CAM_TARGET_GREY={target_grey}")
  env.extend(extra_env)
  return env


def remote_script(openpilot_dir: str, duration: float, settle: float, target_grey: float, extra_env: list[str],
                  isolate: bool, ignore_initial_model_frames: int,
                  capture_dmesg: bool) -> str:
  env_exports = " ".join(bash_env_word(e) for e in remote_env(target_grey, extra_env))
  openpilot_dir_q = shlex.quote(openpilot_dir)
  capture_dmesg_value = "1" if capture_dmesg else "0"
  isolate_cmds = textwrap.dedent("""\
    pkill -f './manager.py' 2>/dev/null || true
    pkill -f 'selfdrive\\.modeld\\.modeld|selfdrive\\.modeld\\.dmonitoringmodeld|selfdrive\\.locationd\\.calibrationd|selfdrive\\.controls\\.controlsd|selfdrive\\.selfdrived\\.selfdrived' 2>/dev/null || true
    pkill -x camerad 2>/dev/null || true
    sleep 1
  """).strip()
  isolate_block = textwrap.indent(isolate_cmds, "    ") if isolate else ""
  return textwrap.dedent(f"""\
    set -e
    lock_dir=/tmp/asius_live_vfe_modeld.lock
    if ! mkdir "$lock_dir" 2>/dev/null; then
      echo "another live VFE/modeld validation is active on this Dragon" >&2
      exit 75
    fi
    camerad_pid=""
    modeld_pid=""
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
      [ -n "$modeld_pid" ] && kill "$modeld_pid" 2>/dev/null || true
      [ -n "$camerad_pid" ] && kill "$camerad_pid" 2>/dev/null || true
      [ -n "$modeld_pid" ] && wait "$modeld_pid" 2>/dev/null || true
      [ -n "$camerad_pid" ] && wait "$camerad_pid" 2>/dev/null || true
      collect_dmesg
      rmdir "$lock_dir" 2>/dev/null || true
    }}
    trap cleanup EXIT

    cd {openpilot_dir_q}
    rm -f {REMOTE_CAMERAD_LOG} {REMOTE_MODELD_LOG} {REMOTE_DMESG_LOG} {REMOTE_SUMMARY}
{isolate_block}
    pkill -x camerad 2>/dev/null || true
    pkill -f 'selfdrive/modeld/modeld.py --demo' 2>/dev/null || true
    if [ "$capture_dmesg" = "1" ] && command -v dmesg >/dev/null 2>&1; then
      dmesg_before_lines=$(dmesg 2>/dev/null | wc -l || echo 0)
    fi
    export PYTHONPATH={openpilot_dir_q}:{openpilot_dir_q}/tinygrad_repo:{openpilot_dir_q}/opendbc_repo
    export LD_LIBRARY_PATH=/opt/qcom-adreno/lib
    export {env_exports}

    ./system/camerad/camerad > {REMOTE_CAMERAD_LOG} 2>&1 &
    camerad_pid=$!
    sleep {settle:.3f}

    python3 selfdrive/modeld/modeld.py --demo > {REMOTE_MODELD_LOG} 2>&1 &
    modeld_pid=$!

    python3 - <<'PY'
    import json
    import statistics
    import time

    import cereal.messaging as messaging

    duration = {duration:.6f}
    ignore_initial_model_frames = {ignore_initial_model_frames:d}
    pm = messaging.PubMaster([
      "deviceState",
      "liveCalibration",
      "carState",
      "carControl",
      "driverMonitoringState",
      "liveDelay",
    ])
    model_sock = messaging.sub_sock("modelV2", conflate=False, timeout=0)
    road_sock = messaging.sub_sock("roadCameraState", conflate=False, timeout=0)
    wide_sock = messaging.sub_sock("wideRoadCameraState", conflate=False, timeout=0)
    time.sleep(0.5)

    disabled_publishers = []
    disabled_publisher_set = set()
    model_frames = []
    model_exec_ms = []
    model_valid = 0
    model_drop_pct = []
    road_frames = []
    wide_frames = []
    road_sensor = None
    wide_sensor = None
    start = time.monotonic()
    sends = 0

    def safe_send(service, msg):
      if service in disabled_publisher_set:
        return
      try:
        pm.send(service, msg)
      except Exception as e:
        if e.__class__.__name__ == "MultiplePublishersError":
          disabled_publisher_set.add(service)
          disabled_publishers.append(service)
          print(f"existing publisher owns {{service}}, using it instead", flush=True)
        else:
          raise

    def publish_inputs():
      device = messaging.new_message("deviceState")
      device.deviceState.deviceType = "one"
      device.deviceState.started = True
      device.deviceState.freeSpacePercent = 90.0
      device.deviceState.memoryUsagePercent = 20
      device.deviceState.thermalStatus = "ok"
      safe_send("deviceState", device)

      calib = messaging.new_message("liveCalibration")
      calib.liveCalibration.calStatus = "calibrated"
      calib.liveCalibration.calPerc = 100
      calib.liveCalibration.validBlocks = 20
      calib.liveCalibration.rpyCalib = [0.0, 0.0, 0.0]
      calib.liveCalibration.rpyCalibSpread = [0.0, 0.0, 0.0]
      calib.liveCalibration.height = [1.22]
      safe_send("liveCalibration", calib)

      car_state = messaging.new_message("carState")
      car_state.carState.vEgo = 0.0
      car_state.carState.standstill = True
      safe_send("carState", car_state)

      car_control = messaging.new_message("carControl")
      car_control.carControl.latActive = False
      safe_send("carControl", car_control)

      dm = messaging.new_message("driverMonitoringState")
      dm.driverMonitoringState.isRHD = False
      dm.driverMonitoringState.alertLevel = "none"
      safe_send("driverMonitoringState", dm)

      delay = messaging.new_message("liveDelay")
      delay.liveDelay.lateralDelay = 0.0
      delay.liveDelay.status = "estimated"
      delay.liveDelay.validBlocks = 20
      delay.liveDelay.calPerc = 100
      safe_send("liveDelay", delay)

    while time.monotonic() - start < duration:
      publish_inputs()
      sends += 1

      for msg in messaging.drain_sock(model_sock):
        m = msg.modelV2
        model_frames.append(int(m.frameId))
        model_exec_ms.append(float(m.modelExecutionTime) * 1000.0)
        model_drop_pct.append(float(m.frameDropPerc))
        if bool(msg.valid):
          model_valid += 1

      for msg in messaging.drain_sock(road_sock):
        r = msg.roadCameraState
        road_frames.append(int(r.frameId))
        road_sensor = str(r.sensor)

      for msg in messaging.drain_sock(wide_sock):
        w = msg.wideRoadCameraState
        wide_frames.append(int(w.frameId))
        wide_sensor = str(w.sensor)

      time.sleep(0.05)

    steady_exec_ms = model_exec_ms[ignore_initial_model_frames:]
    steady_drop_pct = model_drop_pct[ignore_initial_model_frames:]

    summary = {{
      "duration": duration,
      "disabled_support_publishers": disabled_publishers,
      "support_messages_sent": sends,
      "modelV2": {{
        "frames": len(model_frames),
        "valid": model_valid,
        "ignored_initial_frames": ignore_initial_model_frames,
        "first_frame_id": model_frames[0] if model_frames else None,
        "last_frame_id": model_frames[-1] if model_frames else None,
        "max_execution_ms": max(model_exec_ms) if model_exec_ms else None,
        "median_execution_ms": statistics.median(model_exec_ms) if model_exec_ms else None,
        "steady_state_frames": len(steady_exec_ms),
        "steady_state_max_execution_ms": max(steady_exec_ms) if steady_exec_ms else None,
        "steady_state_median_execution_ms": statistics.median(steady_exec_ms) if steady_exec_ms else None,
        "steady_state_max_frame_drop_pct": max(steady_drop_pct) if steady_drop_pct else None,
        "max_frame_drop_pct": max(model_drop_pct) if model_drop_pct else None,
      }},
      "roadCameraState": {{
        "frames": len(road_frames),
        "sensor": road_sensor,
        "first_frame_id": road_frames[0] if road_frames else None,
        "last_frame_id": road_frames[-1] if road_frames else None,
      }},
      "wideRoadCameraState": {{
        "frames": len(wide_frames),
        "sensor": wide_sensor,
        "first_frame_id": wide_frames[0] if wide_frames else None,
        "last_frame_id": wide_frames[-1] if wide_frames else None,
      }},
    }}
    with open("{REMOTE_SUMMARY}", "w") as f:
      json.dump(summary, f, indent=2, sort_keys=True)
    print(json.dumps(summary, indent=2, sort_keys=True))
    PY
  """)


def pull(remote: str, local: Path, timeout: float = DEFAULT_PULL_TIMEOUT) -> bool:
  local.parent.mkdir(parents=True, exist_ok=True)
  cmd = ["scp", *SSH_OPTS, f"comma@{NCM_IP}:{remote}", str(local)]
  for attempt in range(1, 4):
    try:
      subprocess.run(cmd, check=True, timeout=timeout)
      return local.exists()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
      if attempt == 3:
        return False
  return False


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


def camerad_hardware_path_summary(camerad_log: str) -> tuple[dict, list[str]]:
  summary = {
    "fatal_markers": {},
    "cameras": {},
  }
  failures: list[str] = []

  for marker in FATAL_CAMERAD_MARKERS:
    present = marker in camerad_log
    summary["fatal_markers"][marker] = present
    if present:
      failures.append(f"camerad: forbidden fallback marker present: {marker}")

  for cam, cam_num in CAMERA_NUMS.items():
    vfe_pix_v4l2 = re.search(rf"cam {re.escape(cam_num)}: VIPC buffers created \(VFE PIX V4L2", camerad_log) is not None
    dmabuf_nv12 = re.search(rf"cam {re.escape(cam_num)}: VIPC buffers created \(VFE PIX V4L2 DMABUF NV12", camerad_log) is not None
    summary["cameras"][cam] = {
      "camera_num": cam_num,
      "vfe_pix_v4l2": vfe_pix_v4l2,
      "dmabuf_nv12": dmabuf_nv12,
    }
    if not vfe_pix_v4l2:
      failures.append(f"camerad: {cam} missing VFE PIX V4L2 VIPC buffer marker")
    if not dmabuf_nv12:
      failures.append(f"camerad: {cam} missing VFE PIX V4L2 DMABUF NV12 marker")

  return summary, failures


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--openpilot-dir", default="/data/openpilot")
  parser.add_argument("--out-dir", default="/tmp/dragon_os04_bench/live-vfe-modeld")
  parser.add_argument("--duration", type=float, default=25.0)
  parser.add_argument("--settle", type=float, default=7.0)
  parser.add_argument("--target-grey", type=float, default=0.0, help="OS04 AE target grey fraction; 0 uses camerad defaults")
  parser.add_argument("--min-model-frames", type=int, default=20)
  parser.add_argument("--min-camera-frames", type=int, default=20)
  parser.add_argument("--max-model-exec-ms", type=float, default=80.0)
  parser.add_argument("--max-frame-drop-pct", type=float, default=10.0)
  parser.add_argument("--max-sync-warnings", type=int, default=0)
  parser.add_argument("--ignore-initial-model-frames", type=int, default=20)
  parser.add_argument("--check-dmesg", action="store_true",
                      help="capture dmesg during the run and fail on CAMSS/VFE errors, stalls, or buffer-address spam")
  parser.add_argument("--max-dmesg-matches", type=int, default=0,
                      help="maximum forbidden dmesg matches allowed when --check-dmesg is set")
  parser.add_argument("--env", action="append", default=[], metavar="NAME=VALUE")
  parser.add_argument("--pull-timeout", type=float, default=DEFAULT_PULL_TIMEOUT,
                      help="seconds allowed for each SSH/SCP artifact pull before retrying")
  parser.add_argument("--keep-existing-openpilot", action="store_true",
                      help="do not stop an already-running manager/openpilot stack before testing")
  args = parser.parse_args()
  if args.target_grey < 0.0:
    parser.error("--target-grey must be non-negative")
  if args.pull_timeout <= 0.0:
    parser.error("--pull-timeout must be positive")

  out_dir = Path(args.out_dir)
  out_dir.mkdir(parents=True, exist_ok=True)

  script = remote_script(args.openpilot_dir, args.duration, args.settle,
                         args.target_grey,
                         args.env, not args.keep_existing_openpilot,
                         args.ignore_initial_model_frames,
                         args.check_dmesg)
  result = subprocess.run(["ssh", *SSH_OPTS, f"comma@{NCM_IP}", "bash", "-s"], input=script, text=True)

  summary_path = out_dir / LOCAL_SUMMARY
  camerad_log_path = out_dir / LOCAL_CAMERAD_LOG
  modeld_log_path = out_dir / LOCAL_MODELD_LOG
  dmesg_log_path = out_dir / LOCAL_DMESG_LOG
  pull(REMOTE_SUMMARY, summary_path, args.pull_timeout)
  pull(REMOTE_CAMERAD_LOG, camerad_log_path, args.pull_timeout)
  pull(REMOTE_MODELD_LOG, modeld_log_path, args.pull_timeout)
  if args.check_dmesg:
    pull(REMOTE_DMESG_LOG, dmesg_log_path, args.pull_timeout)

  if result.returncode != 0:
    return result.returncode
  if not summary_path.exists():
    print(f"missing remote summary: {summary_path}", file=sys.stderr)
    return 1

  summary = json.loads(summary_path.read_text())
  failures = []

  for name in ("roadCameraState", "wideRoadCameraState"):
    frames = int(summary[name]["frames"])
    sensor = summary[name]["sensor"]
    if frames < args.min_camera_frames:
      failures.append(f"{name}: {frames} frames < {args.min_camera_frames}")
    if sensor != "os04c10":
      failures.append(f"{name}: sensor {sensor!r} != 'os04c10'")

  model = summary["modelV2"]
  model_frames = int(model["frames"])
  if model_frames < args.min_model_frames:
    failures.append(f"modelV2: {model_frames} frames < {args.min_model_frames}")
  if int(model["valid"]) <= 0:
    failures.append("modelV2: no valid messages")
  max_exec = model["steady_state_max_execution_ms"]
  if max_exec is None or float(max_exec) > args.max_model_exec_ms:
    failures.append(f"modelV2: steady-state max execution {max_exec} ms > {args.max_model_exec_ms:.1f} ms")
  max_drop = model["steady_state_max_frame_drop_pct"]
  if max_drop is None or float(max_drop) > args.max_frame_drop_pct:
    failures.append(f"modelV2: steady-state max frame drop {max_drop} > {args.max_frame_drop_pct:.1f}")

  camerad_log = camerad_log_path.read_text(errors="replace") if camerad_log_path.exists() else ""
  hardware_path_summary, hardware_path_failures = camerad_hardware_path_summary(camerad_log)
  summary["camerad_hardware_path"] = hardware_path_summary
  failures.extend(hardware_path_failures)

  modeld_log = modeld_log_path.read_text(errors="replace") if modeld_log_path.exists() else ""
  sync_warnings = modeld_log.count("frames out of sync!")
  if sync_warnings > args.max_sync_warnings:
    failures.append(f"modeld: sync warnings {sync_warnings} > {args.max_sync_warnings}")

  if args.check_dmesg:
    if not dmesg_log_path.exists():
      failures.append(f"dmesg: missing captured log {dmesg_log_path}")
      dmesg_matches = []
    else:
      dmesg_text = dmesg_log_path.read_text(errors="replace")
      dmesg_matches = forbidden_dmesg_matches(dmesg_text)
      if len(dmesg_matches) > args.max_dmesg_matches:
        failures.append(
          f"dmesg: forbidden CAMSS/VFE matches {len(dmesg_matches)} > {args.max_dmesg_matches}"
        )
    summary["dmesg"] = {
      "path": str(dmesg_log_path),
      "checked": True,
      "forbidden_matches": dmesg_matches,
      "max_matches": args.max_dmesg_matches,
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

  print(f"summary: {summary_path}")
  print(f"camerad_log: {camerad_log_path}")
  print(f"modeld_log: {modeld_log_path}")
  if args.check_dmesg:
    print(f"dmesg_log: {dmesg_log_path}")
  print(json.dumps(summary, indent=2, sort_keys=True))

  if failures:
    for failure in failures:
      print(f"FAIL {failure}", file=sys.stderr)
    return 1
  print("PASS live VFE CAM2/CAM3 feeds modeld")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
