#!/usr/bin/env python3
"""Download the signed native openpilot release used by factory images."""
import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]


def download(url: str, target: Path) -> None:
  if not url.startswith("https://"):
    raise ValueError("Release downloads require HTTPS")
  request = Request(url, headers={"User-Agent": "vamos-release/1", "Accept-Encoding": "identity"})
  with urlopen(request, timeout=120) as source, target.open("wb") as output:
    while block := source.read(1024 * 1024):
      output.write(block)


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--manifest", default="https://vamos.asius.ai/openpilot/latest.json")
  parser.add_argument("--output", type=Path, default=ROOT / "build/openpilot/openpilot-v0-chestnut.tar.xz")
  args = parser.parse_args()
  args.output.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory(dir=args.output.parent) as temporary:
    staging = Path(temporary)
    manifest, signature = staging / "release.json", staging / "release.sig"
    download(args.manifest, manifest)
    download(args.manifest + ".sig", signature)
    subprocess.run([
      "openssl", "pkeyutl", "-verify", "-pubin", "-rawin",
      "-inkey", str(ROOT / "userspace/root/usr/share/vamos/update-public.pem"),
      "-in", str(manifest), "-sigfile", str(signature),
    ], check=True)
    release = json.loads(manifest.read_text())
    if release["target"] != "asius-v0" or release["chestnut"] is not True:
      raise ValueError("The release must include the Asius v0 Chestnut models")
    archive = staging / "openpilot.tar.xz"
    download(release["archive_url"], archive)
    with archive.open("rb") as source:
      digest = hashlib.file_digest(source, "sha256").hexdigest()
    if archive.stat().st_size != release["archive_bytes"] or digest != release["archive_sha256"]:
      raise ValueError("Prebuilt archive failed size or SHA-256 verification")
    archive.replace(args.output)
    print(f"Downloaded native openpilot from {release['source_commit']}")


if __name__ == "__main__":
  main()
