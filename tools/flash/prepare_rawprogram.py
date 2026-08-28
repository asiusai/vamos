#!/usr/bin/env python3
"""Materialize verified local flash chunks and an edl-ng rawprogram XML."""

from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree


SUPPORTED_SECTOR_SIZES = (512, 4096)
CACHE_VERSION = 2
CACHE_MARKER = ".prepared.json"


def sha256(data: bytes) -> str:
  return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
  with path.open("rb") as stream:
    return hashlib.file_digest(stream, "sha256").hexdigest()


def operations_for_payload(manifest: dict, payload: str) -> list[dict]:
  sector_size = manifest.get("sector_size")
  if sector_size not in SUPPORTED_SECTOR_SIZES:
    raise ValueError("unsupported local flash sector size")
  if manifest.get("manifest_version") == 1:
    if payload != "base":
      raise ValueError("legacy flash manifests contain one bundled payload")
    operations = manifest["chunks"]
  elif manifest.get("manifest_version") == 2:
    if payload == "base":
      operations = manifest["base"]["operations"]
    else:
      operations = manifest["optional_payloads"]["openpilot"]["operations"]
  else:
    raise ValueError("unsupported local flash manifest")

  if not isinstance(operations, list):
    raise ValueError("flash payload operations must be a list")
  for operation in operations:
    if not isinstance(operation, dict) or operation.get("operation") not in ("erase", "program"):
      raise ValueError("flash payload contains an invalid operation")
    offset = operation.get("offset")
    size = operation.get("size")
    if not isinstance(offset, int) or not isinstance(size, int) or offset < 0 or size <= 0:
      raise ValueError("flash operation has an invalid byte range")
    if offset % sector_size or size % sector_size:
      raise ValueError(f"flash operation is not aligned to {sector_size}-byte sectors")
  return operations


def expected_programs(operations: list[dict]) -> list[dict]:
  return [operation for operation in operations if operation["operation"] == "program"]


def prepared_cache_valid(
  output: Path,
  payload_sha256: str,
  legacy_manifest_sha256: str,
  payload: str,
  programs: list[dict],
) -> dict | None:
  try:
    marker = json.loads((output / CACHE_MARKER).read_text(encoding="utf-8"))
    if marker.get("payload") != payload:
      return None
    if marker.get("cache_version") == CACHE_VERSION:
      if marker.get("payload_sha256") != payload_sha256:
        return None
    elif marker.get("cache_version") == 1:
      if marker.get("manifest_sha256") != legacy_manifest_sha256:
        return None
    else:
      return None

    expected_files = []
    for index, operation in enumerate(programs):
      path = output / f"chunk-{index:05d}.img"
      stat = path.stat()
      expected_files.append({
        "mtime_ns": stat.st_mtime_ns,
        "name": path.name,
        "sha256": operation["sha256"],
        "size": stat.st_size,
      })
      if stat.st_size != operation["size"]:
        return None
      if sha256_file(path) != operation["sha256"]:
        return None
    if marker.get("files") != expected_files:
      return None
    if {path.name for path in output.glob("chunk-*.img")} != {
      entry["name"] for entry in expected_files
    }:
      return None

    rawprogram = (output / "rawprogram0.xml").read_bytes()
    return marker if marker.get("rawprogram_sha256") == sha256(rawprogram) else None
  except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
    return None


def prepare_payload(manifest_path: Path, output: Path, payload: str) -> bool:
  """Materialize one payload, returning True when a verified cache was reused."""
  manifest_bytes = manifest_path.read_bytes()
  manifest = json.loads(manifest_bytes)
  operations = operations_for_payload(manifest, payload)
  programs = expected_programs(operations)
  payload_digest = sha256(json.dumps({
    "operations": operations,
    "sector_size": manifest["sector_size"],
  }, sort_keys=True, separators=(",", ":")).encode())

  cached = prepared_cache_valid(
    output,
    payload_digest,
    sha256(manifest_bytes),
    payload,
    programs,
  )
  if cached is not None:
    if cached["cache_version"] == 1:
      cached["cache_version"] = CACHE_VERSION
      cached["payload_sha256"] = payload_digest
      cached.pop("manifest_sha256", None)
      marker_path = output / CACHE_MARKER
      marker_tmp = output / f"{CACHE_MARKER}.tmp"
      marker_tmp.write_text(
        json.dumps(cached, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
      )
      os.replace(marker_tmp, marker_path)
    print(f"Reusing {len(programs)} verified {payload} program ranges in {output}")
    return True

  temporary = output.with_name(f".{output.name}.tmp")
  if temporary.exists():
    shutil.rmtree(temporary)
  temporary.mkdir(parents=True)
  try:
    objects = manifest_path.parent
    sector_size = manifest["sector_size"]
    root = ElementTree.Element("data", {
      "SECTOR_SIZE_IN_BYTES": str(sector_size),
      "physical_partition_number": "0",
    })

    prepared_files = []
    for index, operation in enumerate(programs):
      source = objects / Path(urlparse(operation["url"]).path).name
      compressed = source.read_bytes()
      if len(compressed) != operation["compressed_size"] or \
         sha256(compressed) != operation["compressed_sha256"]:
        raise ValueError(f"compressed flash object failed verification: {source}")
      raw = lzma.decompress(compressed)
      if len(raw) != operation["size"] or sha256(raw) != operation["sha256"]:
        raise ValueError(f"raw flash object failed verification: {source}")

      filename = f"chunk-{index:05d}.img"
      destination = temporary / filename
      destination.write_bytes(raw)
      stat = destination.stat()
      prepared_files.append({
        "mtime_ns": stat.st_mtime_ns,
        "name": filename,
        "sha256": operation["sha256"],
        "size": stat.st_size,
      })
      ElementTree.SubElement(root, "program", {
        "SECTOR_SIZE_IN_BYTES": str(sector_size),
        "filename": filename,
        "label": f"{payload}-{index}",
        "num_partition_sectors": str(len(raw) // sector_size),
        "physical_partition_number": "0",
        "start_sector": str(operation["offset"] // sector_size),
      })

    tree = ElementTree.ElementTree(root)
    ElementTree.indent(tree, space="  ")
    rawprogram_path = temporary / "rawprogram0.xml"
    tree.write(rawprogram_path, encoding="utf-8", xml_declaration=True)
    marker = {
      "cache_version": CACHE_VERSION,
      "files": prepared_files,
      "payload": payload,
      "payload_sha256": payload_digest,
      "rawprogram_sha256": sha256(rawprogram_path.read_bytes()),
    }
    (temporary / CACHE_MARKER).write_text(
      json.dumps(marker, indent=2, sort_keys=True) + "\n",
      encoding="utf-8",
    )

    if output.exists():
      shutil.rmtree(output)
    os.replace(temporary, output)
  except Exception:
    shutil.rmtree(temporary, ignore_errors=True)
    raise

  print(f"Prepared {len(programs)} verified {payload} program ranges in {output}")
  return False


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("manifest", type=Path)
  parser.add_argument("output", type=Path)
  parser.add_argument("--payload", choices=("base", "openpilot"), required=True)
  args = parser.parse_args()

  try:
    prepare_payload(args.manifest, args.output, args.payload)
  except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, lzma.LZMAError) as error:
    raise SystemExit(str(error)) from error


if __name__ == "__main__":
  main()
