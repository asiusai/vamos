#!/usr/bin/env python3
"""Create a Git-friendly, chunked archive of a packaged vamOS update."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "build/ota"
DEFAULT_OUTPUT = ROOT / "build/vamos-images"
CHUNK_SIZE = 50 * 1024 * 1024
READ_SIZE = 4 * 1024 * 1024


def sha256(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb", buffering=0) as source:
    while chunk := source.read(READ_SIZE):
      digest.update(chunk)
  return digest.hexdigest()


def archive_file(source: Path, output_dir: Path, base_url: str, chunk_size: int = CHUNK_SIZE) -> dict:
  if chunk_size <= 0:
    raise ValueError("chunk size must be positive")

  if source.stat().st_size <= chunk_size:
    shutil.copy2(source, output_dir / source.name)
    return {"url": f"{base_url.rstrip('/')}/{source.name}"}

  chunks: list[dict] = []
  with source.open("rb", buffering=0) as input_file:
    index = 0
    while payload := input_file.read(chunk_size):
      filename = f"{source.name}.{index:02d}"
      destination = output_dir / filename
      destination.write_bytes(payload)
      chunks.append({
        "url": f"{base_url.rstrip('/')}/{filename}",
        "size": len(payload),
      })
      index += 1
  if not chunks:
    raise ValueError(f"cannot archive empty file: {source}")
  return {"url": "", "chunks": chunks}


def archive_update(input_dir: Path, output_dir: Path, source_commit: str, chunk_size: int = CHUNK_SIZE) -> Path:
  manifest_path = input_dir / "vamos.json"
  signature_path = input_dir / "vamos.json.sig"
  manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  if not signature_path.is_file() or signature_path.stat().st_size != 64:
    raise ValueError("vamos.json.sig must be a 64-byte Ed25519 signature")

  partitions = manifest.get("partitions")
  if not isinstance(partitions, list):
    raise ValueError("vamos.json has no partitions list")
  version = str(manifest.get("version"))
  base_url = f"https://github.com/asiusai/vamos-images/raw/v{version}"

  if output_dir.exists() and any(output_dir.iterdir()):
    raise FileExistsError(f"archive output is not empty: {output_dir}")
  output_dir.mkdir(parents=True, exist_ok=True)
  shutil.copy2(manifest_path, output_dir / "vamos.json")
  shutil.copy2(signature_path, output_dir / "vamos.json.sig")

  archived_partitions = []
  for partition in partitions:
    filename = Path(str(partition["url"])).name
    source = input_dir / filename
    if not source.is_file():
      raise FileNotFoundError(f"missing packaged image {source}")
    compressed_hash = sha256(source)
    expected_hash = partition.get("compressed_sha256")
    if expected_hash and compressed_hash != expected_hash:
      raise ValueError(f"compressed hash mismatch for {filename}")
    archived_partition = {
      "name": partition["name"],
      "hash": compressed_hash,
      "hash_raw": partition.get("sha256") or partition.get("hash"),
      "size": partition.get("size"),
      "compressed_size": source.stat().st_size,
      "compression": partition.get("compression", "xz"),
      "sparse": False,
      "full_check": True,
      **archive_file(source, output_dir, base_url, chunk_size),
    }
    archived_partitions.append(archived_partition)

  archive_manifest_path = output_dir / "manifest.json"
  archive_manifest_path.write_text(
    json.dumps(archived_partitions, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
  )
  release = {
    "archive_version": 1,
    "product": manifest.get("product"),
    "version": version,
    "source_commit": source_commit,
    "device_manifest": "vamos.json",
    "archive_manifest": "manifest.json",
  }
  (output_dir / "release.json").write_text(
    json.dumps(release, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
  )
  return archive_manifest_path


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
  parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
  parser.add_argument("--source-commit", required=True, help="vamOS Git commit used for the build")
  args = parser.parse_args()
  manifest = archive_update(args.input, args.output, args.source_commit)
  print(f"Wrote {manifest}")


if __name__ == "__main__":
  main()
