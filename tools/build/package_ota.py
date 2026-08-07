#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = ROOT / "build"
OUTPUT_DIR = BUILD_DIR / "ota"
PRODUCT = "asius-v1"
MANIFEST_VERSION = 1
UPDATER_VERSION = 1
CHUNK_SIZE = 4 * 1024 * 1024
IMAGE_SIZES = {
  "esp": 256 * 1024 * 1024,
  "system": 10 * 1024 * 1024 * 1024,
}


def sha256(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb", buffering=0) as source:
    while chunk := source.read(CHUNK_SIZE):
      digest.update(chunk)
  return digest.hexdigest()


def compress_image(source: Path, destination: Path) -> None:
  temporary = destination.with_suffix(destination.suffix + ".tmp")
  try:
    with temporary.open("wb") as compressed:
      subprocess.run(
        ["xz", "-T0", "-1", "--check=crc64", "--stdout", str(source)],
        check=True,
        stdout=compressed,
      )
    os.replace(temporary, destination)
  finally:
    temporary.unlink(missing_ok=True)


def package_image(name: str, source: Path, base_url: str) -> dict:
  if not source.is_file():
    raise FileNotFoundError(f"missing {source}; build it first")
  if source.stat().st_size != IMAGE_SIZES[name]:
    raise ValueError(f"{source} is {source.stat().st_size} bytes, expected {IMAGE_SIZES[name]}")
  raw_hash = sha256(source)
  filename = f"{name}-{raw_hash}.img.xz"
  destination = OUTPUT_DIR / filename
  if destination.is_file():
    print(f"Reusing {destination.name}")
  else:
    print(f"Compressing {source.name} -> {destination.name}")
    compress_image(source, destination)
  return {
    "name": name,
    "url": f"{base_url.rstrip('/')}/{filename}",
    "size": source.stat().st_size,
    "hash": raw_hash,
    "sha256": raw_hash,
    "compression": "xz",
    "compressed_size": destination.stat().st_size,
    "compressed_sha256": sha256(destination),
  }


def main() -> None:
  parser = argparse.ArgumentParser(description="Package a full vamOS A/B update")
  parser.add_argument(
    "--base-url",
    default=os.environ.get("VAMOS_OTA_BASE_URL", "https://updates.asius.ai/vamos/objects"),
    help="public URL prefix containing the content-addressed image objects",
  )
  parser.add_argument(
    "--version",
    default=(ROOT / "userspace/root/VERSION").read_text(encoding="utf-8").strip(),
  )
  args = parser.parse_args()

  OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
  partitions = [
    package_image("esp", BUILD_DIR / "esp.img", args.base_url),
    package_image("system", BUILD_DIR / "system.img", args.base_url),
  ]
  manifest = {
    "manifest_version": MANIFEST_VERSION,
    "minimum_updater_version": UPDATER_VERSION,
    "product": PRODUCT,
    "version": args.version,
    "partitions": partitions,
  }
  manifest_path = OUTPUT_DIR / "vamos.json"
  manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
  (OUTPUT_DIR / "VERSION").write_text(args.version + "\n", encoding="utf-8")
  print(f"Wrote {manifest_path}")


if __name__ == "__main__":
  main()
