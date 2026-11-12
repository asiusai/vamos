#!/usr/bin/env python3
"""Install the AMD firmware required by the packaged tinygrad, before going offline."""

import argparse
import hashlib
import io
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import zstandard

# Match tinygrad.helpers.fetch_fw. Hashes come from the packaged runtime below.
FIRMWARE_REVISION = "0a6871b19abf5d6e024b5d208b101ae53e7fa0de"
FIRMWARE_URL = f"https://kernel.googlesource.com/pub/scm/linux/kernel/git/firmware/linux-firmware/+archive/{FIRMWARE_REVISION}/amdgpu.tar.gz"


def download() -> bytes:
  for attempt in range(6):
    try:
      with urllib.request.urlopen(FIRMWARE_URL, timeout=60) as response:
        return response.read()
    except urllib.error.HTTPError as error:
      if error.code not in (429, 500, 502, 503, 504) or attempt == 5:
        raise
      delay = 2 ** attempt
      retry_after = error.headers.get("Retry-After", "")
      if retry_after.isdecimal():
        delay = max(delay, int(retry_after))
      if delay > 60:
        raise
      print(f"Retrying firmware archive after HTTP {error.code} in {delay}s", file=sys.stderr)
      error.close()
      time.sleep(delay)
  raise RuntimeError("Firmware download exhausted its retries")


def install(root: Path, hashes: dict[str, str], source: Path | None = None) -> None:
  for name in hashes:
    if Path(name).name != name or not name.endswith(".bin"):
      raise ValueError(f"Invalid firmware name: {name}")

  # One archive avoids rate limits from requesting each firmware blob separately.
  # Read only exact, regular members; never extract archive paths to the filesystem.
  blobs: dict[str, bytes] = {}
  if source is None:
    with tarfile.open(fileobj=io.BytesIO(download()), mode="r:gz") as archive:
      for member in archive:
        if member.name not in hashes:
          continue
        if not member.isfile() or member.name in blobs:
          raise ValueError(f"Invalid firmware archive member: {member.name}")
        with archive.extractfile(member) as stream:
          blobs[member.name] = stream.read()
  else:
    blobs = {name: (source / name).read_bytes() for name in hashes}

  # Verify the complete set before replacing any installed firmware.
  for name, expected in hashes.items():
    if name not in blobs:
      raise ValueError(f"Missing firmware: {name}")
    data = blobs[name]
    if hashlib.sha256(data).hexdigest() != expected:
      raise ValueError(f"Firmware SHA-256 mismatch: {name}")
  destination = root / "lib/firmware/amdgpu"
  destination.mkdir(parents=True, exist_ok=True)
  compressor = zstandard.ZstdCompressor(level=9)
  for name, data in blobs.items():
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
