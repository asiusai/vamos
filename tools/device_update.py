#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ssh_key import default_ssh_key


VAMOS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUILD_DIR = VAMOS_ROOT / "build"
DEFAULT_REMOTE_DIR = "/data/vamos-local"
IMAGE_SIZES = {
  "esp": 256 * 1024 * 1024,
  "system": 10 * 1024 * 1024 * 1024,
}
SSH_OPTIONS = (
  "BatchMode=yes",
  "ConnectTimeout=10",
  "ServerAliveInterval=5",
  "ServerAliveCountMax=2",
  "StrictHostKeyChecking=no",
  "UserKnownHostsFile=/dev/null",
  "GlobalKnownHostsFile=/dev/null",
  "LogLevel=ERROR",
)


class DeviceUpdateError(RuntimeError):
  pass


@dataclass(frozen=True)
class PreparedImage:
  name: str
  source: Path
  compressed: Path
  sha256: str


@dataclass(frozen=True)
class Payload:
  version: str
  version_file: Path
  updater: Path
  images: tuple[PreparedImage, ...]


def command_output(command: list[str], cwd: Path | None = None) -> str:
  try:
    return subprocess.check_output(
      command, cwd=cwd, stderr=subprocess.STDOUT, text=True
    ).strip()
  except subprocess.CalledProcessError as error:
    raise DeviceUpdateError(
      error.output.strip() or f"command failed: {shlex.join(command)}"
    ) from error


def normalize_target(target: str) -> str:
  return target if "@" in target else f"comma@{target}"


def ssh_options(identity: Path | None) -> list[str]:
  options: list[str] = []
  if identity is not None:
    if not identity.is_file():
      raise DeviceUpdateError(f"SSH identity not found: {identity}")
    options += ["-i", str(identity)]
  for option in SSH_OPTIONS:
    options += ["-o", option]
  return options


def sha256_file(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb", buffering=0) as source:
    while chunk := source.read(8 * 1024 * 1024):
      digest.update(chunk)
  return digest.hexdigest()


def validate_image(path: Path, name: str) -> None:
  if not path.is_file():
    raise DeviceUpdateError(
      f"missing {path}; build it with ./vamos build {'system' if name == 'system' else 'esp'}"
    )
  expected_size = IMAGE_SIZES[name]
  actual_size = path.stat().st_size
  if actual_size != expected_size:
    raise DeviceUpdateError(f"{path} is {actual_size} bytes, expected {expected_size}")


def system_image_version(path: Path) -> str:
  result = subprocess.run(
    ["debugfs", "-R", "cat /VERSION", str(path)],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    check=False,
  )
  version = result.stdout.strip()
  if result.returncode != 0 or not version:
    detail = result.stderr.strip() or "VERSION is missing"
    raise DeviceUpdateError(f"cannot read /VERSION from {path}: {detail}")
  return version


def compress_image(
  name: str, source: Path, digest: str, build_dir: Path, cache_dir: Path
) -> Path:
  filename = f"{name}-{digest}.img.xz"
  for directory in (cache_dir, build_dir / "ota"):
    candidate = directory / filename
    if candidate.is_file() and candidate.stat().st_size > 0:
      print(f"[device-update] reusing {candidate}", flush=True)
      return candidate

  cache_dir.mkdir(parents=True, exist_ok=True)
  destination = cache_dir / filename
  temporary = destination.with_suffix(destination.suffix + ".tmp")
  temporary.unlink(missing_ok=True)
  print(f"[device-update] compressing {source.name}", flush=True)
  try:
    with temporary.open("wb") as output:
      subprocess.run(
        ["xz", "-T0", "-1", "--check=crc64", "--stdout", str(source)],
        stdout=output,
        check=True,
      )
    os.replace(temporary, destination)
  finally:
    temporary.unlink(missing_ok=True)
  return destination


def prepare_payload(build_dir: Path) -> Payload:
  system = build_dir / "system.img"
  esp = build_dir / "esp.img"
  validate_image(system, "system")
  validate_image(esp, "esp")

  version = system_image_version(system)
  cache_dir = build_dir / "device-update"
  prepared: list[PreparedImage] = []
  for name, source in (("system", system), ("esp", esp)):
    print(f"[device-update] hashing {source}", flush=True)
    digest = sha256_file(source)
    compressed = compress_image(name, source, digest, build_dir, cache_dir)
    prepared.append(PreparedImage(name, source, compressed, digest))

  cache_dir.mkdir(parents=True, exist_ok=True)
  version_file = cache_dir / "VERSION"
  version_file.write_text(version + "\n", encoding="utf-8")
  updater = VAMOS_ROOT / "userspace/root/usr/lib/vamos/update.py"
  if not updater.is_file():
    raise DeviceUpdateError(f"local updater not found: {updater}")
  return Payload(version, version_file, updater, tuple(prepared))


def remote_command(
  target: str, options: list[str], command: list[str], *, capture: bool = False
) -> str:
  ssh = ["ssh", *options, target, shlex.join(command)]
  if capture:
    return command_output(ssh)
  subprocess.run(ssh, check=True)
  return ""


def preflight_remote(target: str, options: list[str], remote_dir: str) -> None:
  if not remote_dir.startswith("/data/"):
    raise DeviceUpdateError("--remote-dir must be below /data")
  probe = (
    "test -f /ASIUS && "
    "sudo -n true && "
    "test -b /dev/disk/by-partlabel/rootfs_a && "
    "test -b /dev/disk/by-partlabel/rootfs_b && "
    f"mkdir -p {shlex.quote(remote_dir)} && "
    f"test -w {shlex.quote(remote_dir)}"
  )
  command_output(["ssh", *options, target, probe])
  remote_command(
    target,
    options,
    ["rm", "-f", f"{remote_dir}/system.img", f"{remote_dir}/esp.img"],
  )


def rsync_file(
  source: Path,
  target: str,
  options: list[str],
  destination: str,
) -> None:
  ssh = shlex.join(["ssh", *options])
  subprocess.run(
    [
      "rsync",
      "-ah",
      "--partial",
      "--info=progress2",
      "--chmod=F644",
      "-e",
      ssh,
      str(source),
      f"{target}:{destination}",
    ],
    check=True,
  )


def sync_payload(
  payload: Payload,
  target: str,
  options: list[str],
  remote_dir: str,
) -> None:
  files = (
    (payload.updater, "update.py"),
    (payload.version_file, "VERSION"),
    *((image.compressed, f"{image.name}.img.xz") for image in payload.images),
  )
  for source, name in files:
    print(f"[device-update] rsync {name}", flush=True)
    rsync_file(source, target, options, f"{remote_dir}/{name}")


def install_remote(
  target: str,
  options: list[str],
  remote_dir: str,
) -> None:
  print("[device-update] writing and verifying the inactive vamOS slot", flush=True)
  remote_command(
    target,
    options,
    [
      "sudo",
      "-n",
      "python3",
      f"{remote_dir}/update.py",
      "local",
      remote_dir,
    ],
  )
  print("[device-update] rebooting into the trial slot", flush=True)
  try:
    remote_command(target, options, ["sudo", "-n", "reboot"])
  except subprocess.CalledProcessError as error:
    if error.returncode != 255:
      raise


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(
    description="Deploy locally built vamOS images to an SSH-connected Dragon"
  )
  parser.add_argument("target", help="SSH target, for example comma@192.168.88.20")
  parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD_DIR)
  parser.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR)
  parser.add_argument(
    "--identity",
    type=Path,
    default=default_ssh_key(),
  )
  args = parser.parse_args(argv)

  try:
    target = normalize_target(args.target)
    options = ssh_options(args.identity)
    build_dir = args.build_dir.expanduser().resolve()

    payload = prepare_payload(build_dir)
    compressed_size = sum(image.compressed.stat().st_size for image in payload.images)
    print(
      f"[device-update] target={target} vamOS={payload.version} transfer={compressed_size / (1024**3):.2f} GiB",
      flush=True,
    )
    preflight_remote(target, options, args.remote_dir)
    sync_payload(payload, target, options, args.remote_dir)
    install_remote(target, options, args.remote_dir)
    print("[device-update] reboot requested; the device will trial the new slot")
    return 0
  except (
    DeviceUpdateError,
    FileNotFoundError,
    subprocess.CalledProcessError,
  ) as error:
    print(f"device-update: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
