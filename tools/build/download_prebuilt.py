#!/usr/bin/env python3
"""Fetch the compiled openpilot release-ci checkout for factory images."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "https://github.com/asiusai/openpilot.git"


def validate_checkout(root: Path) -> dict:
  metadata = json.loads((root / "build.json").read_text())
  if metadata.get("channel") != "release-ci" or metadata.get("openpilot", {}).get("build_style") != "prebuilt":
    raise ValueError("Factory images require the compiled release-ci checkout")
  if not re.fullmatch(r"[0-9a-f]{40}", metadata["openpilot"].get("git_commit", "")):
    raise ValueError("Missing openpilot source commit")
  if not (root / "prebuilt").is_file() or (root / ".gitmodules").exists():
    raise ValueError("The prebuilt checkout must contain flattened dependencies")
  subprocess.run(["git", "-C", str(root), "fsck", "--full"], check=True)
  subprocess.run(["git", "-C", str(root), "lfs", "fsck"], check=True)
  if subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], text=True).strip():
    raise ValueError("The prebuilt checkout must be clean")
  return metadata


def fetch_prebuilt(output: Path, repository: str = REPOSITORY) -> None:
  output.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
    staging = Path(temporary)
    checkout = staging / "checkout"
    environment = dict(os.environ)
    environment.pop("GIT_LFS_SKIP_SMUDGE", None)
    subprocess.run(["git", "clone", "--depth=1", "--single-branch", "--branch=release-ci",
                    repository, str(checkout)], check=True, env=environment)
    metadata = validate_checkout(checkout)
    # Keep each model once in the factory payload, including its LFS cache.
    files = json.loads(subprocess.check_output(["git", "-C", str(checkout), "lfs", "ls-files", "--json"], text=True))
    for model in files["files"]:
      oid = model["oid"]
      if model["oid_type"] != "sha256" or not re.fullmatch(r"[0-9a-f]{64}", oid):
        raise ValueError("Unexpected LFS object format")
      working = checkout / model["name"]
      cached = checkout / ".git/lfs/objects" / oid[:2] / oid[2:4] / oid
      for path in (working, cached):
        with path.open("rb") as stream:
          if hashlib.file_digest(stream, "sha256").hexdigest() != oid:
            raise ValueError(f"Invalid LFS file: {model['name']}")
      cached.unlink()
      os.link(working, cached)
    archive = staging / "openpilot.tar.xz"
    subprocess.run(["tar", "--sort=name", "-cJf", str(archive), "-C", str(checkout), "."],
                   check=True, env={**environment, "XZ_OPT": "-1 -T0 --block-size=16MiB"})
    archive.replace(output)
    print(f"Packaged release-ci from openpilot source {metadata['openpilot']['git_commit']}")


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--output", type=Path, default=ROOT / "build/openpilot/openpilot-v0-chestnut.tar.xz")
  args = parser.parse_args()
  fetch_prebuilt(args.output.resolve())


if __name__ == "__main__":
  main()
