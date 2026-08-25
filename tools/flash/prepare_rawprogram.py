#!/usr/bin/env python3
"""Materialize verified local flash chunks and an edl-ng rawprogram XML."""

from __future__ import annotations

import argparse
import hashlib
import json
import lzma
import shutil
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree


SUPPORTED_SECTOR_SIZES = (512, 4096)


def sha256(data: bytes) -> str:
  return hashlib.sha256(data).hexdigest()


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


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("manifest", type=Path)
  parser.add_argument("output", type=Path)
  parser.add_argument("--payload", choices=("base", "openpilot"), required=True)
  args = parser.parse_args()

  manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
  try:
    operations = operations_for_payload(manifest, args.payload)
  except (KeyError, TypeError, ValueError) as error:
    raise SystemExit(str(error)) from error

  if args.output.exists():
    shutil.rmtree(args.output)
  args.output.mkdir(parents=True)
  objects = args.manifest.parent
  sector_size = manifest["sector_size"]
  root = ElementTree.Element("data", {
    "SECTOR_SIZE_IN_BYTES": str(sector_size),
    "physical_partition_number": "0",
  })

  program_index = 0
  for operation in operations:
    if operation["operation"] != "program":
      continue
    source = objects / Path(urlparse(operation["url"]).path).name
    compressed = source.read_bytes()
    if len(compressed) != operation["compressed_size"] or sha256(compressed) != operation["compressed_sha256"]:
      raise SystemExit(f"compressed flash object failed verification: {source}")
    raw = lzma.decompress(compressed)
    if len(raw) != operation["size"] or sha256(raw) != operation["sha256"]:
      raise SystemExit(f"raw flash object failed verification: {source}")

    filename = f"chunk-{program_index:05d}.img"
    (args.output / filename).write_bytes(raw)
    ElementTree.SubElement(root, "program", {
      "SECTOR_SIZE_IN_BYTES": str(sector_size),
      "filename": filename,
      "label": f"{args.payload}-{program_index}",
      "num_partition_sectors": str(len(raw) // sector_size),
      "physical_partition_number": "0",
      "start_sector": str(operation["offset"] // sector_size),
    })
    program_index += 1

  tree = ElementTree.ElementTree(root)
  ElementTree.indent(tree, space="  ")
  tree.write(args.output / "rawprogram0.xml", encoding="utf-8", xml_declaration=True)
  print(f"Prepared {program_index} verified {args.payload} program ranges in {args.output}")


if __name__ == "__main__":
  main()
