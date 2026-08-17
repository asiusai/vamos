#!/usr/bin/env python3
"""Package the compact Asius v1 factory image for browser EDL flashing."""

from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUILD_DIR = ROOT / "build"
OUTPUT_DIR = BUILD_DIR / "flash"
PRODUCT = "asius-v1"
MANIFEST_VERSION = 1
CHUNK_SIZE = 64 * 1024 * 1024
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


def package_image(source: Path, base_url: str) -> tuple[list[dict], str, int]:
  if not source.is_file():
    raise FileNotFoundError(f"missing {source}; run ./vamos build disk first")
  image_size = source.stat().st_size
  if image_size == 0 or image_size % SECTOR_SIZE:
    raise ValueError(f"factory image size must be a non-zero multiple of {SECTOR_SIZE}")

  image_hash = hashlib.sha256()
  chunks: list[dict] = []
  offset = 0
  with source.open("rb", buffering=0) as image:
    while raw := image.read(CHUNK_SIZE):
      image_hash.update(raw)
      raw_hash = sha256_bytes(raw)
      chunk = {
        "offset": offset,
        "sha256": raw_hash,
        "size": len(raw),
      }
      if not any(raw):
        chunk["operation"] = "erase"
      else:
        compressed = lzma.compress(raw, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=1)
        compressed_hash = sha256_bytes(compressed)
        filename = f"chunk-{compressed_hash}.img.xz"
        destination = OUTPUT_DIR / filename
        if not destination.exists():
          atomic_write(destination, compressed)
        chunk.update(
          {
            "compressed_sha256": compressed_hash,
            "compressed_size": len(compressed),
            "compression": "xz",
            "operation": "program",
            "url": f"{base_url.rstrip('/')}/{filename}",
          }
        )
      chunks.append(chunk)
      offset += len(raw)

  return chunks, image_hash.hexdigest(), image_size


def main() -> None:
  parser = argparse.ArgumentParser(description="Package a signed-manifest-ready Asius v1 browser flash image")
  parser.add_argument(
    "--base-url",
    default=os.environ.get("VAMOS_FLASH_BASE_URL", "https://updates.asius.ai/vamos/flash/objects"),
  )
  parser.add_argument("--image", type=Path, default=BUILD_DIR / "dragon.img")
  parser.add_argument("--programmer", type=Path, default=PROGRAMMER)
  parser.add_argument(
    "--version",
    default=(ROOT / "userspace/root/VERSION").read_text(encoding="utf-8").strip(),
  )
  args = parser.parse_args()

  OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
  chunks, image_hash, image_size = package_image(args.image, args.base_url)
  manifest = {
    "chunk_size": CHUNK_SIZE,
    "image": {
      "sha256": image_hash,
      "size": image_size,
    },
    "manifest_version": MANIFEST_VERSION,
    "product": PRODUCT,
    "programmer": package_programmer(args.programmer, args.base_url),
    "sector_size": SECTOR_SIZE,
    "storage_profiles": STORAGE_PROFILES,
    "version": args.version,
    "chunks": chunks,
  }
  manifest_path = OUTPUT_DIR / "flash.json"
  atomic_write(manifest_path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
  print(f"Wrote {manifest_path}: {len(chunks)} chunks, {image_size} bytes")


if __name__ == "__main__":
  main()
