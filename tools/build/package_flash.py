#!/usr/bin/env python3
"""Package the common factory disk and optional openpilot userdata delta."""

from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path
from typing import BinaryIO


ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = ROOT / "build"
OUTPUT_DIR = BUILD_DIR / "flash"
PRODUCT = "asius-v1"
MANIFEST_VERSION = 2
# Keep each browser allocation modest while avoiding thousands of individual
# HTTP requests and Firehose program commands for a source-complete payload.
CHUNK_SIZE = 16 * 1024 * 1024
SECTOR_SIZE = 512
PROGRAMMER = ROOT / "firmware-dragon/flat_build/spinor/dragon-q6a/prog_firehose_ddr.elf"

# Asius v1 currently ships this 128 GB 2230 NVMe. The signed manifest carries
# this allowlist so a generic Qualcomm EDL device cannot be selected and
# accidentally overwritten.
STORAGE_PROFILES = [
  {
    "block_size": 512,
    "manufacturer_id": 642131526,
    "mem_type": "NVMe",
    "num_physical": 1,
    "page_size": 512,
    "prod_name": "KINGSTON OM3PGP4128P-AH",
    "total_blocks": 250069680,
  },
]


def sha256_bytes(data: bytes) -> str:
  return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
  temporary = path.with_suffix(path.suffix + ".tmp")
  try:
    temporary.write_bytes(data)
    os.replace(temporary, path)
  finally:
    temporary.unlink(missing_ok=True)


def package_programmer(source: Path, base_url: str) -> dict:
  if not source.is_file():
    raise FileNotFoundError(f"missing Firehose programmer: {source}")
  data = source.read_bytes()
  if data.startswith(b"version https://git-lfs.github.com/spec/v1"):
    raise ValueError(f"{source} is a Git LFS pointer; fetch LFS objects first")
  digest = sha256_bytes(data)
  filename = f"programmer-{digest}.bin"
  atomic_write(OUTPUT_DIR / filename, data)
  return {
    "sha256": digest,
    "size": len(data),
    "url": f"{base_url.rstrip('/')}/{filename}",
  }


def program_operation(raw: bytes, offset: int, base_url: str) -> dict:
  raw_hash = sha256_bytes(raw)
  # Name by the raw content so an interrupted or repeated release can reuse a
  # previously compressed object without spending minutes recompressing it.
  filename = f"chunk-{raw_hash}.img.xz"
  destination = OUTPUT_DIR / filename
  if destination.exists():
    compressed = destination.read_bytes()
    try:
      if lzma.decompress(compressed) != raw:
        raise ValueError(f"cached flash object does not match its name: {destination}")
    except lzma.LZMAError as error:
      raise ValueError(f"cached flash object is invalid: {destination}") from error
  else:
    compressed = lzma.compress(raw, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=1)
    atomic_write(destination, compressed)
  compressed_hash = sha256_bytes(compressed)
  return {
    "compressed_sha256": compressed_hash,
    "compressed_size": len(compressed),
    "compression": "xz",
    "offset": offset,
    "operation": "program",
    "sha256": raw_hash,
    "size": len(raw),
    "url": f"{base_url.rstrip('/')}/{filename}",
  }


def read_chunk(source: BinaryIO) -> bytes:
  return source.read(CHUNK_SIZE)


def package_base_image(source: Path, base_url: str) -> tuple[list[dict], int]:
  """Erase the target, then package only non-zero ranges from the common disk."""
  if not source.is_file():
    raise FileNotFoundError(f"missing {source}; run ./vamos build disk first")
  image_size = source.stat().st_size
  if image_size == 0 or image_size % SECTOR_SIZE:
    raise ValueError(f"factory image size must be a non-zero multiple of {SECTOR_SIZE}")

  operations: list[dict] = [{"offset": 0, "operation": "erase", "size": image_size}]
  offset = 0
  with source.open("rb", buffering=0) as image:
    while raw := read_chunk(image):
      if raw.count(0) != len(raw):
        operations.append(program_operation(raw, offset, base_url))
      offset += len(raw)

  return operations, image_size


def package_delta(
  baseline: Path,
  updated: Path,
  target_offset: int,
  base_url: str,
) -> tuple[list[dict], int]:
  """Package changed ranges needed to turn baseline into updated exactly."""
  for image in (baseline, updated):
    if not image.is_file():
      raise FileNotFoundError(f"missing {image}; run ./vamos build disk first")
  image_size = baseline.stat().st_size
  if updated.stat().st_size != image_size:
    raise ValueError("optional userdata images have different sizes")
  if image_size == 0 or image_size % SECTOR_SIZE or target_offset % SECTOR_SIZE:
    raise ValueError("optional userdata payload is not sector-aligned")

  operations: list[dict] = []
  relative_offset = 0
  with baseline.open("rb", buffering=0) as before, updated.open("rb", buffering=0) as after:
    while new := read_chunk(after):
      old = before.read(len(new))
      if len(old) != len(new):
        raise ValueError("optional userdata baseline ended early")
      if old != new:
        offset = target_offset + relative_offset
        if new.count(0) != len(new):
          operations.append(program_operation(new, offset, base_url))
        else:
          operations.append({"offset": offset, "operation": "erase", "size": len(new)})
      relative_offset += len(new)
    if before.read(1):
      raise ValueError("optional userdata update ended early")

  return operations, image_size


def load_layout(path: Path) -> dict:
  try:
    layout = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError) as error:
    raise ValueError(f"invalid factory layout {path}: {error}") from error
  if layout.get("sector_size") != SECTOR_SIZE:
    raise ValueError("factory layout has the wrong sector size")
  userdata = layout.get("partitions", {}).get("userdata", {})
  if not isinstance(userdata.get("start_sector"), int) or userdata["start_sector"] <= 0:
    raise ValueError("factory layout has no userdata start sector")
  return layout


def prune_unreferenced_objects(manifest: dict) -> None:
  """Keep rerun output upload-safe without discarding reusable objects early."""
  referenced = {manifest["programmer"]["url"].rsplit("/", 1)[-1]}
  payloads = [manifest["base"], *manifest["optional_payloads"].values()]
  for payload in payloads:
    for operation in payload["operations"]:
      if operation["operation"] == "program":
        referenced.add(operation["url"].rsplit("/", 1)[-1])
  for pattern in ("chunk-*.img.xz", "programmer-*.bin"):
    for path in OUTPUT_DIR.glob(pattern):
      if path.name not in referenced:
        path.unlink()


def main() -> None:
  parser = argparse.ArgumentParser(description="Package a signed-manifest-ready Asius v1 browser flash image")
  parser.add_argument(
    "--base-url",
    default=os.environ.get("VAMOS_FLASH_BASE_URL", "https://updates.asius.ai/vamos/flash/objects"),
  )
  parser.add_argument("--image", type=Path, default=BUILD_DIR / "dragon.img")
  parser.add_argument("--layout", type=Path, default=BUILD_DIR / "factory-layout.json")
  parser.add_argument("--userdata", type=Path, default=BUILD_DIR / "userdata.img")
  parser.add_argument("--openpilot-userdata", type=Path, default=BUILD_DIR / "userdata-openpilot.img")
  parser.add_argument("--programmer", type=Path, default=PROGRAMMER)
  parser.add_argument(
    "--version",
    default=(ROOT / "userspace/root/VERSION").read_text(encoding="utf-8").strip(),
  )
  args = parser.parse_args()

  OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
  layout = load_layout(args.layout)
  operations, image_size = package_base_image(args.image, args.base_url)
  userdata_offset = layout["partitions"]["userdata"]["start_sector"] * SECTOR_SIZE
  openpilot_operations, userdata_size = package_delta(
    args.userdata,
    args.openpilot_userdata,
    userdata_offset,
    args.base_url,
  )
  manifest = {
    "base": {
      "image": {"size": image_size},
      "operations": operations,
    },
    "chunk_size": CHUNK_SIZE,
    "manifest_version": MANIFEST_VERSION,
    "optional_payloads": {
      "openpilot": {
        "description": "Preloaded Asius openpilot source; first boot builds it locally.",
        "label": "vamOS + openpilot",
        "operations": openpilot_operations,
        "result": {"size": userdata_size},
        "target": "userdata",
      },
    },
    "product": PRODUCT,
    "programmer": package_programmer(args.programmer, args.base_url),
    "sector_size": SECTOR_SIZE,
    "storage_profiles": STORAGE_PROFILES,
    "version": args.version,
  }
  manifest_path = OUTPUT_DIR / "flash.json"
  atomic_write(manifest_path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
  prune_unreferenced_objects(manifest)
  print(
    f"Wrote {manifest_path}: {len(operations)} base operations, "
    f"{len(openpilot_operations)} optional openpilot operations, {image_size} bytes"
  )


if __name__ == "__main__":
  main()
