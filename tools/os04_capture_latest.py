#!/usr/bin/env python3
"""Capture one OS04 RAW10 frame from Dragon and write stable preview files."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


WIDTH = 2688
HEIGHT = 1520
SSH_OPTS = [
  "-i", os.path.expanduser("~/.ssh/comma_setup"),
  "-o", "StrictHostKeyChecking=no",
  "-o", "UserKnownHostsFile=/dev/null",
  "-o", "GlobalKnownHostsFile=/dev/null",
  "-o", "LogLevel=ERROR",
]


def exposure_overrides(lines: int) -> list[str]:
  if lines < 1 or lines > 0xfffff:
    raise ValueError("--exposure-lines must be in 1..1048575")
  return [
    f"0x3500=0x{(lines >> 12) & 0xff:02x}",
    f"0x3501=0x{(lines >> 4) & 0xff:02x}",
    f"0x3502=0x{(lines & 0x0f) << 4:02x}",
  ]


def run_capture(vamos_dir: Path, cam: str, label: str, overrides: list[str]) -> None:
  cmd = [
    str(vamos_dir / "tools/os04_raw_bench_log.py"),
    "--label", label,
    "--",
    "--cam", cam,
    "--frames", "1",
    "--polls", "50",
    "--poll-ms", "100",
    "--skip-testgen",
  ]
  for override in overrides:
    cmd.extend(["--override", override])
  subprocess.run(cmd, cwd=vamos_dir, check=True)


def pull_raw(cam: str, out_path: Path) -> None:
  remote = f"comma@192.168.42.2:/tmp/{cam}-os04-raw.raw"
  subprocess.run(["scp", *SSH_OPTS, remote, str(out_path)], check=True)


def unpack_raw10(raw_path: Path) -> np.ndarray:
  data = np.fromfile(raw_path, dtype=np.uint8)
  expected = WIDTH * HEIGHT * 5 // 4
  if data.size != expected:
    raise ValueError(f"{raw_path}: got {data.size} bytes, expected {expected}")

  packed = data.reshape(HEIGHT, WIDTH // 4, 5).astype(np.uint16)
  img10 = np.empty((HEIGHT, WIDTH), dtype=np.uint16)
  img10[:, 0::4] = (packed[:, :, 0] << 2) | ((packed[:, :, 4] >> 0) & 0x03)
  img10[:, 1::4] = (packed[:, :, 1] << 2) | ((packed[:, :, 4] >> 2) & 0x03)
  img10[:, 2::4] = (packed[:, :, 2] << 2) | ((packed[:, :, 4] >> 4) & 0x03)
  img10[:, 3::4] = (packed[:, :, 3] << 2) | ((packed[:, :, 4] >> 6) & 0x03)
  return img10


def stretch_to_u8(img10: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
  percentiles = np.percentile(img10, [0, 0.1, 1, 50, 99, 99.9, 100])
  lo = float(percentiles[2])
  hi = float(percentiles[4])
  if hi <= lo:
    lo = float(img10.min())
    hi = float(img10.max() or 1)
  mono8 = np.clip((img10.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)
  return mono8, percentiles, lo, hi


def interp_channel(channel: np.ndarray, mask: np.ndarray) -> np.ndarray:
  h, w = channel.shape
  ch = channel.astype(np.float32)
  m = mask.astype(np.float32)
  total = np.zeros_like(ch)
  weight = np.zeros_like(ch)
  padded_ch = np.pad(ch, 1, mode="edge")
  padded_m = np.pad(m, 1, mode="edge")
  for dy in range(3):
    for dx in range(3):
      total += padded_ch[dy:dy + h, dx:dx + w]
      weight += padded_m[dy:dy + h, dx:dx + w]
  return total / np.maximum(weight, 1.0)


def demosaic_preview(mono8: np.ndarray, pattern: str) -> Image.Image:
  h, w = mono8.shape
  masks = {c: np.zeros((h, w), dtype=bool) for c in "RGB"}
  tile = [[pattern[0], pattern[1]], [pattern[2], pattern[3]]]
  for ymod in (0, 1):
    for xmod in (0, 1):
      masks[tile[ymod][xmod]][ymod::2, xmod::2] = True

  base = mono8.astype(np.float32)
  channels = [
    interp_channel(np.where(masks[c], base, 0), masks[c])
    for c in "RGB"
  ]
  arr = np.stack(channels, axis=2)

  means = [
    arr[:, :, i][masks["RGB"[i]]].mean() if masks["RGB"[i]].any() else 1.0
    for i in range(3)
  ]
  gray = sum(means) / 3.0
  for i, mean in enumerate(means):
    if mean > 1:
      arr[:, :, i] *= gray / mean
  return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def write_previews(img10: np.ndarray, out_dir: Path, prefix: str) -> tuple[np.ndarray, float, float]:
  mono8, percentiles, lo, hi = stretch_to_u8(img10)
  Image.fromarray(mono8, "L").save(out_dir / f"{prefix}-gray.png")

  items = [
    ("GRAY", Image.fromarray(mono8, "L").convert("RGB").resize((672, 380), Image.Resampling.BILINEAR)),
  ]
  for pattern in ("BGGR", "RGGB", "GRBG", "GBRG"):
    im = demosaic_preview(mono8, pattern)
    im.save(out_dir / f"{prefix}-{pattern.lower()}.png")
    items.append((pattern, im.resize((672, 380), Image.Resampling.BILINEAR)))

  canvas = Image.new("RGB", (1344, 1140), (18, 18, 18))
  draw = ImageDraw.Draw(canvas)
  for idx, (label, image) in enumerate(items):
    x = (idx % 2) * 672
    y = (idx // 2) * 380
    canvas.paste(image, (x, y))
    draw.rectangle((x, y, x + 150, y + 30), fill=(0, 0, 0))
    draw.text((x + 8, y + 8), label, fill=(255, 255, 255))
  canvas.save(out_dir / f"{prefix}-contact.png")
  return percentiles, lo, hi


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument("--cam", default="cam2", choices=("cam1", "cam2", "cam3"))
  parser.add_argument("--out-dir", default="/tmp/dragon_os04_bench")
  parser.add_argument("--prefix", default=None, help="stable output prefix, default latest-CAM")
  parser.add_argument("--exposure-lines", type=int, help="override 0x3500/01/02 exposure lines")
  parser.add_argument("--override", action="append", default=[], help="extra sensor addr=value override")
  args = parser.parse_args()

  vamos_dir = Path(__file__).resolve().parents[1]
  out_dir = Path(args.out_dir)
  out_dir.mkdir(parents=True, exist_ok=True)
  prefix = args.prefix or f"latest-{args.cam}"

  overrides = list(args.override)
  if args.exposure_lines is not None:
    overrides.extend(exposure_overrides(args.exposure_lines))

  label = prefix
  if args.exposure_lines is not None:
    label += f"-exp{args.exposure_lines}"

  run_capture(vamos_dir, args.cam, label, overrides)
  raw_path = out_dir / f"{prefix}.raw"
  pull_raw(args.cam, raw_path)
  img10 = unpack_raw10(raw_path)
  percentiles, lo, hi = write_previews(img10, out_dir, prefix)

  print(f"raw={raw_path}")
  print(f"gray={out_dir / f'{prefix}-gray.png'}")
  print(f"contact={out_dir / f'{prefix}-contact.png'}")
  print("percentiles_10bit=" + ", ".join(f"{value:.1f}" for value in percentiles))
  print(f"stretch_1_99={lo:.1f}..{hi:.1f}")
  if percentiles[-1] >= 1023 or percentiles[-2] >= 1000:
    print("note=highlights clipped; try --exposure-lines 20 or lower")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
