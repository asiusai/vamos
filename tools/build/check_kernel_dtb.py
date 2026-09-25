#!/usr/bin/env python3
"""Reject duplicate firmware reservations that prevent remoteproc from probing."""

from pathlib import Path
import struct
import subprocess
import sys


def check_reservations(path: Path) -> None:
  def get(node: str, prop: str) -> list[int]:
    result = subprocess.check_output(["fdtget", "-t", "x", str(path), node, prop], text=True)
    return [int(value, 16) for value in result.split()]

  def cells(values: list[int]) -> int:
    result = 0
    for value in values:
      result = result << 32 | value
    return result

  blob = path.read_bytes()
  magic, size, _, _, offset = struct.unpack_from(">5I", blob)
  if magic != 0xD00DFEED or size > len(blob):
    raise ValueError("invalid DTB header")
  reservations = []
  while True:
    start, length = struct.unpack_from(">QQ", blob, offset)
    offset += 16
    if not length:
      break
    reservations.append((start, start + length))

  address_cells = get("/reserved-memory", "#address-cells")[0]
  size_cells = get("/reserved-memory", "#size-cells")[0]
  children = subprocess.check_output(["fdtget", "-l", str(path), "/reserved-memory"], text=True)
  for child in children.splitlines():
    node = f"/reserved-memory/{child}"
    properties = subprocess.check_output(["fdtget", "-p", str(path), node], text=True).splitlines()
    if "no-map" not in properties or "reg" not in properties:
      continue
    reg = get(node, "reg")
    width = address_cells + size_cells
    if len(reg) % width:
      raise ValueError(f"invalid reg property: {node}")
    for offset in range(0, len(reg), width):
      start = cells(reg[offset:offset + address_cells])
      end = start + cells(reg[offset + address_cells:offset + width])
      for low, high in reservations:
        if start < high and low < end:
          raise ValueError(f"{node} overlaps /memreserve/ {low:#x}-{high:#x}; reserve firmware gaps separately")


if __name__ == "__main__":
  check_reservations(Path(sys.argv[1]))
