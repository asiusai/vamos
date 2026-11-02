#!/usr/bin/env python3
"""Install the AMD firmware required by the packaged tinygrad, before going offline."""

import argparse
import base64
import hashlib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import zstandard

# Match tinygrad.helpers.fetch_fw. Hashes come from the packaged runtime below.
FIRMWARE_REVISION = "0a6871b19abf5d6e024b5d208b101ae53e7fa0de"
FIRMWARE_URL = f"https://kernel.googlesource.com/pub/scm/linux/kernel/git/firmware/linux-firmware/+/{FIRMWARE_REVISION}/amdgpu"


def install(root: Path, hashes: dict[str, str], source: Path | None = None) -> None:
  def load(item: tuple[str, str]) -> tuple[str, bytes]:
    name, expected = item
    if Path(name).name != name or not name.endswith(".bin"):
      raise ValueError(f"Invalid firmware name: {name}")
    if source is None:
      with urllib.request.urlopen(f"{FIRMWARE_URL}/{name}?format=TEXT", timeout=30) as response:
        data = base64.b64decode(response.read(), validate=True)
    else:
      data = (source / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
      raise ValueError(f"Firmware SHA-256 mismatch: {name}")
    return name, data

  # Verify the complete set before replacing any installed firmware.
  with ThreadPoolExecutor(max_workers=4) as pool:
    blobs = list(pool.map(load, hashes.items()))
  destination = root / "lib/firmware/amdgpu"
  destination.mkdir(parents=True, exist_ok=True)
  compressor = zstandard.ZstdCompressor(level=9)
  for name, data in blobs:
    target = destination / f"{name}.zst"
    temporary = target.with_suffix(".zst.tmp")
    temporary.write_bytes(compressor.compress(data))
    temporary.chmod(0o644)
    temporary.replace(target)
  print(f"Installed {len(blobs)} verified AMD firmware files")


if __name__ == "__main__":
  from tinygrad.runtime.autogen.am.fw import hashes

  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--root", type=Path, default=Path("/"))
  parser.add_argument("--source", type=Path, help="Directory of previously downloaded firmware files")
  args = parser.parse_args()
  install(args.root, hashes, args.source)
