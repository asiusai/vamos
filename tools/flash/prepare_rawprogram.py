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


SECTOR_SIZE = 512


def sha256(data: bytes) -> str:
  return hashlib.sha256(data).hexdigest()


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("manifest", type=Path)
  parser.add_argument("output", type=Path)
  parser.add_argument("--payload", choices=("base", "openpilot"), required=True)
  args = parser.parse_args()

  manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
  if manifest.get("manifest_version") != 2 or manifest.get("sector_size") != SECTOR_SIZE:
    raise SystemExit("unsupported local flash manifest")
  if args.payload == "base":
    operations = manifest["base"]["operations"]
  else:
    operations = manifest["optional_payloads"]["openpilot"]["operations"]

  if args.output.exists():
    shutil.rmtree(args.output)
  args.output.mkdir(parents=True)
  objects = args.manifest.parent
  root = ElementTree.Element("data", {
    "SECTOR_SIZE_IN_BYTES": str(SECTOR_SIZE),
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
      "SECTOR_SIZE_IN_BYTES": str(SECTOR_SIZE),
      "filename": filename,
      "label": f"{args.payload}-{program_index}",
      "num_partition_sectors": str(len(raw) // SECTOR_SIZE),
      "physical_partition_number": "0",
      "start_sector": str(operation["offset"] // SECTOR_SIZE),
    })
    program_index += 1

  tree = ElementTree.ElementTree(root)
  ElementTree.indent(tree, space="  ")
  tree.write(args.output / "rawprogram0.xml", encoding="utf-8", xml_declaration=True)
  print(f"Prepared {program_index} verified {args.payload} program ranges in {args.output}")


if __name__ == "__main__":
  main()
