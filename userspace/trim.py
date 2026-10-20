#!/usr/bin/env python3
"""Trim the staged image after its final dependency installation."""
import argparse
from pathlib import Path
import shutil
import subprocess


def trim(root: Path, strip: str) -> None:
  site = root / "usr/local/venv/lib/python3.12/site-packages"
  if not site.is_dir() or not (root / "ASIUS").is_file():
    raise ValueError("expected a staged Asius root filesystem")

  # Match AGNOS's allowlist. Keep numpy.testing, sympy.testing and build tools.
  for package in ("sympy", "numpy", "matplotlib", "mpmath", "setuptools"):
    for path in sorted((site / package).rglob("*"), key=lambda p: len(p.parts)):
      if path.name in ("test", "tests") and path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)

  # GNU strip damages the patched TinyMesa layout. Use LLVM strip instead.
  for package in ("tinymesa", "capnp", "zstandard", "kiwisolver", "fontTools"):
    for path in sorted((site / package).rglob("*.so")):
      if path.is_file() and not path.is_symlink():
        subprocess.run([strip, "--strip-debug", str(path)], check=True)

  # Raylib's bundled Mesa lacks the Dragon msm driver. Use the system Mesa
  # libraries for both EGL users and raylib, including hardware rendering.
  raylib = site / "raylib/install/lib"
  for name in ("libEGL.so.1", "libGLESv2.so.2", "libdrm.so.2"):
    source = root / "usr/lib" / name
    destination = raylib / name
    if not source.exists() or not (destination.exists() or destination.is_symlink()):
      raise ValueError(f"missing graphics runtime: {name}")
    destination.unlink()
    destination.symlink_to(f"/usr/lib/{name}")
  for path in raylib.glob("libgallium-*.so"):
    path.unlink()

  # These Vulkan drivers target other GPUs. Keep freedreno and the software
  # renderer for Dragon operation and diagnostics.
  for driver in ("asahi", "broadcom", "intel", "intel_hasvk", "nouveau", "panfrost", "radeon"):
    (root / f"usr/lib/libvulkan_{driver}.so").unlink(missing_ok=True)
    for path in (root / "usr/share/vulkan/icd.d").glob(f"{driver}_icd*.json"):
      path.unlink()


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("root", type=Path)
  parser.add_argument("--strip", default="llvm-strip")
  args = parser.parse_args()
  trim(args.root.resolve(), args.strip)
