#!/usr/bin/env python3
"""Run or parse the Dragon OS04 raw bench wrapper with a compact verdict.

This is a host-side helper. It wraps tools/os04_raw_bench.sh, keeps the full
terminal transcript, and writes a short summary focused on the hardware
bring-up decision points:

- did CSID testgen produce frames?
- did the external sensor run start the sensor?
- did external frames arrive?
- did CSID RX/RDI IRQ status move even if frames stayed at zero?

It still uses the raw /tmp/camss_rdi_probe path through os04_raw_bench.sh. It
does not use camerad, Spectra/CamX, CamX userspace, or openpilot.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


CSID_ADDR_LABELS = {
  0x0ACB3020: "rx_irq",
  0x0ACB3040: "rdi0_irq",
  0x0ACB3100: "rx_cfg0",
  0x0ACB3104: "rx_cfg1",
  0x0ACB3300: "rdi0_cfg0",
  0x0ACB3308: "rdi0_ctrl",
  0x0ACBA020: "rx_irq",
  0x0ACBA040: "rdi0_irq",
  0x0ACBA100: "rx_cfg0",
  0x0ACBA104: "rx_cfg1",
  0x0ACBA300: "rdi0_cfg0",
  0x0ACBA308: "rdi0_ctrl",
}


@dataclass
class CamResult:
  cam: str
  control_frames: int | None = None
  external_frames: int | None = None
  external_exit: int | None = None
  sensor_start_rc: int | None = None
  init_mismatches: list[int] = field(default_factory=list)
  devmem: dict[str, dict[str, str]] = field(default_factory=dict)
  verdict: str = "UNKNOWN"


def slugify(label: str) -> str:
  slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", label.strip()).strip("-")
  return slug or "raw-bench"


def parse_log(text: str) -> dict[str, CamResult]:
  cams: dict[str, CamResult] = {}
  current_cam: str | None = None
  section: str | None = None
  devmem_section: str | None = None

  for raw_line in text.splitlines():
    line = raw_line.rstrip()

    match = re.match(r"^=== (cam[123]): raw receiver control ===$", line)
    if match:
      current_cam = match.group(1)
      cams.setdefault(current_cam, CamResult(cam=current_cam))
      section = "control"
      devmem_section = None
      continue

    match = re.match(r"^=== (cam[123]): external OS04 raw ===$", line)
    if match:
      current_cam = match.group(1)
      cams.setdefault(current_cam, CamResult(cam=current_cam))
      section = "external"
      devmem_section = None
      continue

    match = re.match(r"^=== (cam[123]): external raw exit (-?\d+) ===$", line)
    if match:
      cam = cams.setdefault(match.group(1), CamResult(cam=match.group(1)))
      cam.external_exit = int(match.group(2))
      current_cam = match.group(1)
      section = "external"
      devmem_section = None
      continue

    if current_cam is None:
      continue

    cam = cams[current_cam]

    match = re.search(r"\bframes=(\d+)\b", line)
    if match:
      frames = int(match.group(1))
      if section == "control":
        cam.control_frames = frames
      elif section == "external":
        cam.external_frames = frames

    match = re.search(r"\bsensor_start rc=(-?\d+)\b", line)
    if match and section == "external":
      cam.sensor_start_rc = int(match.group(1))

    match = re.search(r"\bmismatches=(\d+)\b", line)
    if match and section == "external":
      cam.init_mismatches.append(int(match.group(1)))

    if line in (
      "devmem_read_after_video_streamon",
      "devmem_read_after_sensor_start",
      "devmem_read_after_poll",
    ):
      devmem_section = line
      cam.devmem.setdefault(devmem_section, {})
      continue

    match = re.match(r"^\s+0x([0-9a-fA-F]+)=0x([0-9a-fA-F]+)$", line)
    if match and devmem_section:
      addr = int(match.group(1), 16)
      value = int(match.group(2), 16)
      label = CSID_ADDR_LABELS.get(addr, f"0x{addr:08x}")
      cam.devmem[devmem_section][label] = f"0x{value:08x}"

  for cam in cams.values():
    cam.verdict = verdict_for(cam)
  return cams


def _hex_nonzero(value: str | None) -> bool:
  if value is None:
    return False
  return int(value, 16) != 0


def verdict_for(cam: CamResult) -> str:
  if cam.control_frames is not None and cam.control_frames < 1:
    return "CONTROL_FAIL"
  if cam.init_mismatches and any(value != 0 for value in cam.init_mismatches):
    return "INIT_MISMATCH"
  if cam.sensor_start_rc is not None and cam.sensor_start_rc != 0:
    return "SENSOR_START_FAIL"
  if cam.external_frames is not None and cam.external_frames >= 1:
    return "FRAMES"

  irq_values: list[str] = []
  for section_name in ("devmem_read_after_sensor_start", "devmem_read_after_poll"):
    values = cam.devmem.get(section_name, {})
    for label in ("rx_irq", "rdi0_irq"):
      if label in values:
        irq_values.append(values[label])

  if irq_values and any(_hex_nonzero(value) for value in irq_values):
    return "CSID_ACTIVITY_NO_FRAMES"
  if irq_values:
    return "NO_CSI_ACTIVITY"
  if cam.external_frames == 0:
    return "ZERO_FRAMES_NO_CSID_READS"
  return "UNKNOWN"


def summary_lines(cams: dict[str, CamResult], log_path: Path | None,
                  run_rc: int | None) -> list[str]:
  lines: list[str] = []
  if log_path:
    lines.append(f"log={log_path}")
  if run_rc is not None:
    lines.append(f"run_rc={run_rc}")

  for cam_name in sorted(cams):
    cam = cams[cam_name]
    after_start = cam.devmem.get("devmem_read_after_sensor_start", {})
    after_poll = cam.devmem.get("devmem_read_after_poll", {})
    rx_irq = after_poll.get("rx_irq", after_start.get("rx_irq", "missing"))
    rdi0_irq = after_poll.get("rdi0_irq", after_start.get("rdi0_irq", "missing"))
    rx_cfg0 = after_start.get("rx_cfg0", after_poll.get("rx_cfg0", "missing"))
    rdi0_cfg0 = after_start.get("rdi0_cfg0", after_poll.get("rdi0_cfg0", "missing"))
    lines.append(
      " ".join([
        f"{cam_name}:",
        f"verdict={cam.verdict}",
        f"control_frames={value_or_missing(cam.control_frames)}",
        f"external_frames={value_or_missing(cam.external_frames)}",
        f"sensor_start_rc={value_or_missing(cam.sensor_start_rc)}",
        f"external_exit={value_or_missing(cam.external_exit)}",
        f"rx_irq={rx_irq}",
        f"rdi0_irq={rdi0_irq}",
        f"rx_cfg0={rx_cfg0}",
        f"rdi0_cfg0={rdi0_cfg0}",
      ])
    )
  return lines


def value_or_missing(value: int | None) -> str:
  return "missing" if value is None else str(value)


def write_outputs(out_dir: Path, stem: str, text: str,
                  cams: dict[str, CamResult], run_rc: int | None) -> tuple[Path, Path, Path]:
  out_dir.mkdir(parents=True, exist_ok=True)
  log_path = out_dir / f"{stem}.log"
  summary_path = out_dir / f"{stem}.summary.txt"
  json_path = out_dir / f"{stem}.summary.json"

  log_path.write_text(text)
  lines = summary_lines(cams, log_path, run_rc)
  summary_path.write_text("\n".join(lines) + "\n")
  json_path.write_text(json.dumps({
    "log": str(log_path),
    "run_rc": run_rc,
    "cameras": {name: asdict(result) for name, result in sorted(cams.items())},
  }, indent=2, sort_keys=True) + "\n")
  return log_path, summary_path, json_path


SAMPLE_LOG = """\
=== cam3: raw receiver control ===
CSID test generator mode 1: skipping sensor init/start
video streamon ok, CSID testgen active; polling without sensor start
frame 0 index=0 bytesused=5107200 seq=1 ts=1.000000
frames=1 output=/tmp/cam3-csid-testgen.raw
=== cam3: external OS04 raw ===
after_init mismatches=0
devmem_read_after_video_streamon
  0x0acba020=0x00000000
  0x0acba040=0x00000000
  0x0acba100=0x00300101
  0x0acba300=0x802bf007
video streamon ok, starting sensor
sensor_start rc=0
devmem_read_after_sensor_start
  0x0acba020=0x00000000
  0x0acba040=0x00000000
  0x0acba100=0x00300101
  0x0acba300=0x802bf007
devmem_read_after_poll
  0x0acba020=0x00000000
  0x0acba040=0x00000000
  0x0acba100=0x00300101
  0x0acba300=0x802bf007
frames=0 output=none
=== cam3: external raw exit 2 ===
"""


def self_test() -> int:
  cams = parse_log(SAMPLE_LOG)
  cam3 = cams.get("cam3")
  if cam3 is None:
    print("self-test failed: cam3 missing", file=sys.stderr)
    return 1
  checks = {
    "control_frames": cam3.control_frames == 1,
    "external_frames": cam3.external_frames == 0,
    "sensor_start_rc": cam3.sensor_start_rc == 0,
    "rx_cfg0": cam3.devmem["devmem_read_after_poll"]["rx_cfg0"] == "0x00300101",
    "verdict": cam3.verdict == "NO_CSI_ACTIVITY",
  }
  failed = [name for name, ok in checks.items() if not ok]
  if failed:
    print(f"self-test failed: {', '.join(failed)}", file=sys.stderr)
    return 1
  print("\n".join(summary_lines(cams, None, 2)))
  return 0


def main() -> int:
  parser = argparse.ArgumentParser(
    description="Run or parse the Dragon OS04 raw bench wrapper with a compact verdict.",
  )
  parser.add_argument("--label", default="raw-bench",
                      help="label used in output file names")
  parser.add_argument("--out-dir", default="/tmp/dragon_os04_bench",
                      help="directory for log and summary files")
  parser.add_argument("--wrapper", default="tools/os04_raw_bench.sh",
                      help="raw bench wrapper path, relative to vamos by default")
  parser.add_argument("--from-log", help="parse an existing log instead of running")
  parser.add_argument("--self-test", action="store_true",
                      help="run parser self-test and exit")
  args, bench_args = parser.parse_known_args()
  if bench_args and bench_args[0] == "--":
    bench_args = bench_args[1:]

  if args.self_test:
    return self_test()

  vamos_dir = Path(__file__).resolve().parents[1]
  out_dir = Path(args.out_dir)
  stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
  stem = f"{stamp}-{slugify(args.label)}"

  if args.from_log:
    log_path = Path(args.from_log)
    text = log_path.read_text(errors="replace")
    cams = parse_log(text)
    _, summary_path, json_path = write_outputs(out_dir, stem, text, cams, None)
    for line in summary_lines(cams, log_path, None):
      print(line)
    print(f"summary={summary_path}")
    print(f"json={json_path}")
    return 0

  wrapper = Path(args.wrapper)
  if not wrapper.is_absolute():
    wrapper = vamos_dir / wrapper
  cmd = [str(wrapper), *bench_args]

  header = [
    f"# timestamp_utc={stamp}",
    f"# cwd={vamos_dir}",
    "# cmd=" + " ".join(cmd),
    "",
  ]
  header_text = "\n".join(header)
  captured = [header_text]
  print(header_text, end="")

  process = subprocess.Popen(
    cmd,
    cwd=vamos_dir,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1,
  )
  assert process.stdout is not None
  for line in process.stdout:
    print(line, end="")
    captured.append(line)
  run_rc = process.wait()

  text = "".join(captured)
  cams = parse_log(text)
  log_path, summary_path, json_path = write_outputs(out_dir, stem, text, cams, run_rc)

  print("=== parsed summary ===")
  for line in summary_lines(cams, log_path, run_rc):
    print(line)
  print(f"summary={summary_path}")
  print(f"json={json_path}")
  return run_rc


if __name__ == "__main__":
  raise SystemExit(main())
