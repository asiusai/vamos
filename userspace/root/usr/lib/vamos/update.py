#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import lzma
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterator, Sequence


UPDATER_VERSION = 1
MANIFEST_VERSION = 1
PRODUCT = "radxa-dragon-q6a"
DISK = Path("/dev/nvme0n1")
PARTLABEL_DIR = Path("/dev/disk/by-partlabel")
STATE_DIR = Path("/data/vamos-update")
STATE_FILE = STATE_DIR / "state.json"
HISTORY_FILE = STATE_DIR / "history.jsonl"
LOCK_FILE = Path("/run/lock/vamos-update.lock")
CMDLINE_FILE = Path("/proc/cmdline")
TRIAL_MARKER = Path("/run/vamos-trial-boot")
HEALTHY_MARKER = Path("/run/vamos-boot-healthy")
STAGE1_MARKER = Path("/run/vamos-stage1-ready")
WATCHDOG_PID_FILE = Path("/run/vamos-watchdog.pid")
WATCHDOG_LOG = Path("/run/vamos-watchdog.log")
WATCHDOG_DEVICE = Path("/dev/watchdog0")
WATCHDOG_READY_MARKER = Path("/run/vamos-watchdog-ready")
WATCHDOG_DISARMED_MARKER = Path("/run/vamos-watchdog-disarmed")
UPDATE_PUBLIC_KEY = Path("/usr/share/vamos/update-public.pem")
BOOT_NEXT_VARIABLE = Path("/sys/firmware/efi/efivars/BootNext-8be4df61-93ca-11d2-aa0d-00e098032b8c")

ESP_SIZE = 256 * 1024 * 1024
SYSTEM_SIZE = 10 * 1024 * 1024 * 1024
IO_CHUNK_SIZE = 4 * 1024 * 1024
MAX_MANIFEST_SIZE = 1024 * 1024
ED25519_SIGNATURE_SIZE = 64
EFI_LOADER = r"\EFI\BOOT\BOOTAA64.EFI"
EFI_LABEL = {"a": "vamOS A", "b": "vamOS B"}
EFI_TRIAL_LABEL = {"a": "vamOS A trial", "b": "vamOS B trial"}
EFI_PARTITION = {"a": 1, "b": 3}
ROOT_LABEL = {"a": "rootfs_a", "b": "rootfs_b"}
ESP_LABEL = {"a": "esp_a", "b": "esp_b"}
KERNEL_ARGS = (
  "console=ttyMSM0,115200n8 earlycon "
  "root={root} rootwait rw fw_devlink=permissive quiet loglevel=3 "
  "vamos.slot={slot}"
)


class UpdateError(RuntimeError):
  pass


@dataclass(frozen=True)
class ImageSpec:
  name: str
  source: str
  size: int
  sha256: str
  compression: str


@dataclass(frozen=True)
class Manifest:
  version: str
  images: tuple[ImageSpec, ...]
  source: str


def log(message: str) -> None:
  print(f"vamos-update: {message}", flush=True)


def run(command: Sequence[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
  return subprocess.run(
    list(command),
    check=check,
    text=True,
    stdout=subprocess.PIPE if capture else None,
    stderr=subprocess.STDOUT if capture else None,
  )


def atomic_write_json(path: Path, value: object) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as output:
      json.dump(value, output, indent=2, sort_keys=True)
      output.write("\n")
      output.flush()
      os.fsync(output.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
      os.fsync(directory_fd)
    finally:
      os.close(directory_fd)
  finally:
    with contextlib.suppress(FileNotFoundError):
      os.unlink(temporary)


def load_state() -> dict:
  try:
    with STATE_FILE.open(encoding="utf-8") as state_file:
      value = json.load(state_file)
    return value if isinstance(value, dict) else {}
  except (FileNotFoundError, json.JSONDecodeError):
    return {}


def save_state(state: dict, event: str | None = None) -> None:
  state = dict(state)
  state["updated_at"] = int(time.time())
  atomic_write_json(STATE_FILE, state)
  if event is not None:
    record = dict(state)
    record["event"] = event
    with HISTORY_FILE.open("a", encoding="utf-8") as history:
      history.write(json.dumps(record, sort_keys=True) + "\n")
      history.flush()
      os.fsync(history.fileno())


@contextlib.contextmanager
def update_lock() -> Iterator[None]:
  LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
  with LOCK_FILE.open("w") as lock:
    try:
      fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
      raise UpdateError("another vamOS update operation is already running") from exc
    yield


def sha256_file(path: Path, size: int | None = None, progress_callback: Callable[[int], None] | None = None) -> str:
  digest = hashlib.sha256()
  remaining = size
  read = 0
  last_progress = -1
  with path.open("rb", buffering=0) as source:
    while remaining is None or remaining > 0:
      read_size = IO_CHUNK_SIZE if remaining is None else min(IO_CHUNK_SIZE, remaining)
      chunk = source.read(read_size)
      if not chunk:
        break
      digest.update(chunk)
      read += len(chunk)
      if remaining is not None:
        remaining -= len(chunk)
      if progress_callback is not None and size is not None and size > 0:
        progress = int(read * 100 / size)
        if progress >= last_progress + 5 or progress == 100:
          progress_callback(progress)
          last_progress = progress
  if remaining not in (None, 0):
    raise UpdateError(f"{path} is shorter than the expected {size} bytes")
  return digest.hexdigest()


def _read_url_bytes(source: str, maximum_size: int) -> bytes:
  request = urllib.request.Request(source, headers={"Accept-Encoding": "identity", "User-Agent": "vamos-update/1"})
  with urllib.request.urlopen(request, timeout=60) as response:
    payload = response.read(maximum_size + 1)
  if len(payload) > maximum_size:
    raise UpdateError(f"{source} exceeds the {maximum_size}-byte size limit")
  return payload


def verify_manifest_signature(payload: bytes, signature: bytes) -> None:
  if len(signature) != ED25519_SIGNATURE_SIZE:
    raise UpdateError("update manifest has an invalid Ed25519 signature size")
  if not UPDATE_PUBLIC_KEY.is_file():
    raise UpdateError(f"update signing key is missing: {UPDATE_PUBLIC_KEY}")

  with tempfile.TemporaryDirectory(prefix="vamos-signature-") as temporary:
    manifest_path = Path(temporary) / "manifest"
    signature_path = Path(temporary) / "signature"
    manifest_path.write_bytes(payload)
    signature_path.write_bytes(signature)
    result = run([
      "openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(UPDATE_PUBLIC_KEY),
      "-rawin", "-in", str(manifest_path), "-sigfile", str(signature_path),
    ], check=False)
  if result.returncode != 0:
    raise UpdateError("update manifest signature verification failed")


def _read_json_source(source: str) -> tuple[object, str]:
  parsed = urllib.parse.urlparse(source)
  if parsed.scheme in ("http", "https"):
    payload = _read_url_bytes(source, MAX_MANIFEST_SIZE)
    signature = _read_url_bytes(source + ".sig", ED25519_SIGNATURE_SIZE)
    verify_manifest_signature(payload, signature)
    return json.loads(payload), source

  path = Path(parsed.path if parsed.scheme == "file" else source).expanduser().resolve()
  payload = path.read_bytes()
  if len(payload) > MAX_MANIFEST_SIZE:
    raise UpdateError(f"{path} exceeds the {MAX_MANIFEST_SIZE}-byte size limit")
  signature_path = Path(str(path) + ".sig")
  if signature_path.is_file():
    verify_manifest_signature(payload, signature_path.read_bytes())
  return json.loads(payload), path.as_uri()


def _resolve_source(base: str, value: str) -> str:
  parsed = urllib.parse.urlparse(value)
  if parsed.scheme:
    return value
  return urllib.parse.urljoin(base, value)


def _parse_image(raw: object, base: str) -> ImageSpec:
  if not isinstance(raw, dict):
    raise UpdateError("manifest partition entries must be JSON objects")
  try:
    name = str(raw["name"])
    source = str(raw.get("url") or raw.get("path"))
    size = int(raw["size"])
    sha256 = str(raw.get("sha256") or raw.get("hash_raw") or raw["hash"]).lower()
  except (KeyError, TypeError, ValueError) as exc:
    raise UpdateError(f"invalid manifest partition: {raw!r}") from exc

  compression = str(raw.get("compression", "xz" if source.endswith(".xz") else "none")).lower()
  if name not in ("esp", "system"):
    raise UpdateError(f"unsupported partition name {name!r}")
  if size != {"esp": ESP_SIZE, "system": SYSTEM_SIZE}[name]:
    raise UpdateError(f"{name} image has unsafe size {size}")
  if not re.fullmatch(r"[0-9a-f]{64}", sha256):
    raise UpdateError(f"{name} image has an invalid SHA-256")
  if compression not in ("none", "xz"):
    raise UpdateError(f"{name} image has unsupported compression {compression!r}")
  if not source or source == "None":
    raise UpdateError(f"{name} image has no source")
  return ImageSpec(name, _resolve_source(base, source), size, sha256, compression)


def load_manifest(source: str) -> Manifest:
  raw, base = _read_json_source(source)
  if isinstance(raw, list):
    # Accept comma-style manifests for local tooling compatibility.
    partitions = raw
    version = "unspecified"
    manifest_version = 1
    product = PRODUCT
  elif isinstance(raw, dict):
    partitions = raw.get("partitions")
    version = str(raw.get("version", "unspecified"))
    manifest_version = raw.get("manifest_version")
    product = raw.get("product")
    minimum_updater = int(raw.get("minimum_updater_version", 1))
    if minimum_updater > UPDATER_VERSION:
      raise UpdateError(f"manifest requires updater version {minimum_updater}")
  else:
    raise UpdateError("manifest must be an object or a comma-style partition list")

  if manifest_version != MANIFEST_VERSION:
    raise UpdateError(f"unsupported manifest version {manifest_version!r}")
  if product != PRODUCT:
    raise UpdateError(f"manifest is for {product!r}, expected {PRODUCT!r}")
  if not isinstance(partitions, list):
    raise UpdateError("manifest has no partitions list")

  images = tuple(_parse_image(partition, base) for partition in partitions)
  names = [image.name for image in images]
  if sorted(names) != ["esp", "system"] or len(names) != len(set(names)):
    raise UpdateError("manifest must contain exactly one esp and one system image")
  return Manifest(version, images, source)


@contextlib.contextmanager
def open_image(spec: ImageSpec) -> Iterator[BinaryIO]:
  parsed = urllib.parse.urlparse(spec.source)
  raw: BinaryIO
  response = None
  if parsed.scheme in ("http", "https"):
    request = urllib.request.Request(spec.source, headers={"Accept-Encoding": "identity", "User-Agent": "vamos-update/1"})
    response = urllib.request.urlopen(request, timeout=60)
    raw = response
  elif parsed.scheme == "file":
    raw = open(urllib.request.url2pathname(parsed.path), "rb", buffering=0)
  elif not parsed.scheme:
    raw = open(spec.source, "rb", buffering=0)
  else:
    raise UpdateError(f"unsupported image URL scheme {parsed.scheme!r}")

  try:
    if spec.compression == "xz":
      with lzma.LZMAFile(raw, "rb") as decompressed:
        yield decompressed
    else:
      yield raw
  finally:
    if response is not None:
      response.close()
    else:
      raw.close()


def inspect_local_image(path: Path, name: str) -> ImageSpec:
  compression = "xz" if path.name.endswith(".xz") else "none"
  temporary = ImageSpec(name, path.resolve().as_uri(), {"esp": ESP_SIZE, "system": SYSTEM_SIZE}[name], "0" * 64, compression)
  digest = hashlib.sha256()
  size = 0
  with open_image(temporary) as source:
    while chunk := source.read(IO_CHUNK_SIZE):
      size += len(chunk)
      if size > temporary.size:
        raise UpdateError(f"{path} expands beyond {temporary.size} bytes")
      digest.update(chunk)
  if size != temporary.size:
    raise UpdateError(f"{path} expands to {size} bytes, expected {temporary.size}")
  return ImageSpec(name, temporary.source, size, digest.hexdigest(), compression)


def manifest_from_directory(directory: Path) -> Manifest:
  directory = directory.expanduser().resolve()
  images: list[ImageSpec] = []
  for name in ("esp", "system"):
    candidates = [directory / f"{name}.img.xz", directory / f"{name}.img"]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
      raise UpdateError(f"{directory} has no {name}.img or {name}.img.xz")
    log(f"hashing local {path.name}")
    images.append(inspect_local_image(path, name))

  version = f"local-{int(time.time())}"
  version_file = directory / "VERSION"
  if version_file.is_file():
    version = version_file.read_text(encoding="utf-8").strip()
  return Manifest(version, tuple(images), directory.as_uri() + "/")


def cmdline() -> str:
  return CMDLINE_FILE.read_text(encoding="utf-8").strip()


def current_slot() -> str:
  match = re.search(r"(?:^|\s)vamos\.slot=([ab])(?:\s|$)", cmdline())
  if match:
    return match.group(1)

  result = run(["findmnt", "-n", "-o", "SOURCE", "/"]).stdout.strip()
  for slot, label in ROOT_LABEL.items():
    if label in result or Path(result).resolve() == (PARTLABEL_DIR / label).resolve():
      return slot
  raise UpdateError("cannot determine active vamOS slot")


def partition_path(slot: str, image_name: str) -> Path:
  label = ESP_LABEL[slot] if image_name == "esp" else ROOT_LABEL[slot]
  path = PARTLABEL_DIR / label
  if not path.exists():
    raise UpdateError(f"required partition {label} does not exist")
  return path


def block_size(path: Path) -> int:
  return int(run(["blockdev", "--getsize64", str(path)]).stdout.strip())


def write_image(spec: ImageSpec, destination: Path,
                progress_callback: Callable[[str, int], None] | None = None) -> None:
  actual_size = block_size(destination)
  if actual_size < spec.size:
    raise UpdateError(f"{destination} is {actual_size} bytes, smaller than {spec.size}")

  digest = hashlib.sha256()
  written = 0
  last_progress = -1
  log(f"writing {spec.name} to {destination}")
  with open_image(spec) as source, destination.open("r+b", buffering=0) as output:
    while chunk := source.read(IO_CHUNK_SIZE):
      written += len(chunk)
      if written > spec.size:
        raise UpdateError(f"{spec.name} expands beyond declared size")
      output.write(chunk)
      digest.update(chunk)
      progress = int(written * 100 / spec.size)
      if progress >= last_progress + 5:
        log(f"{spec.name}: {progress}%")
        if progress_callback is not None:
          progress_callback("writing", progress)
        last_progress = progress
    output.flush()
    os.fsync(output.fileno())

  if written != spec.size:
    raise UpdateError(f"{spec.name} wrote {written} bytes, expected {spec.size}")
  if digest.hexdigest() != spec.sha256:
    raise UpdateError(f"{spec.name} source SHA-256 mismatch")

  log(f"verifying {spec.name} from disk")
  actual_hash = sha256_file(
    destination,
    spec.size,
    None if progress_callback is None else lambda progress: progress_callback("verifying", progress),
  )
  if actual_hash != spec.sha256:
    raise UpdateError(f"{spec.name} on-device SHA-256 mismatch")


@contextlib.contextmanager
def mounted_read_only(device: Path) -> Iterator[Path]:
  with tempfile.TemporaryDirectory(prefix="vamos-verify-") as mount_dir:
    mount_path = Path(mount_dir)
    run(["mount", "-o", "ro,noload", str(device), str(mount_path)])
    try:
      yield mount_path
    finally:
      run(["umount", str(mount_path)], capture=False)


def verify_system_contents(device: Path, expected_version: str) -> None:
  check = run(["e2fsck", "-fn", str(device)], check=False)
  if check.returncode != 0:
    raise UpdateError(f"system image filesystem check failed:\n{check.stdout.strip()}")
  with mounted_read_only(device) as root:
    required = (
      root / "VAMOS",
      root / "VERSION",
      root / "etc/runit/1",
      root / "usr/comma/comma.sh",
      root / "usr/bin/vamos-update",
      root / "usr/bin/vamos-boot",
      root / "usr/lib/vamos/update.py",
      root / "usr/share/vamos/update-public.pem",
    )
    missing = [str(path.relative_to(root)) for path in required if not path.exists()]
    if missing:
      raise UpdateError(f"system image lacks rollback support: {', '.join(missing)}")
    if not os.access(root / "etc/runit/1", os.X_OK):
      raise UpdateError("system image runit stage 1 is not executable")
    if not os.access(root / "usr/comma/comma.sh", os.X_OK):
      raise UpdateError("system image launcher is not executable")
    actual_version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if expected_version not in ("", "unspecified") and actual_version != expected_version:
      raise UpdateError(f"system image version {actual_version!r} does not match manifest {expected_version!r}")


def verify_esp_contents(device: Path) -> None:
  check = run(["fsck.vfat", "-n", str(device)], check=False)
  if check.returncode != 0:
    raise UpdateError(f"ESP filesystem check failed:\n{check.stdout.strip()}")
  with tempfile.TemporaryDirectory(prefix="vamos-esp-") as mount_dir:
    mount_path = Path(mount_dir)
    run(["mount", "-o", "ro", str(device), str(mount_path)])
    try:
      required = (
        mount_path / "EFI/BOOT/BOOTAA64.EFI",
        mount_path / "qcs6490-radxa-dragon-q6a.dtb",
      )
      missing = [str(path.relative_to(mount_path)) for path in required if not path.is_file()]
      if missing:
        raise UpdateError(f"ESP image is not bootable: {', '.join(missing)}")
      verify_arm64_efi(required[0])
      if required[1].read_bytes()[:4] != b"\xd0\r\xfe\xed":
        raise UpdateError("ESP device tree has an invalid FDT header")
    finally:
      run(["umount", str(mount_path)], capture=False)


def verify_arm64_efi(path: Path) -> None:
  with path.open("rb") as image:
    header = image.read(4096)
  if len(header) < 64 or header[:2] != b"MZ":
    raise UpdateError("ESP kernel has no EFI MZ header")
  pe_offset = struct.unpack_from("<I", header, 0x3c)[0]
  if pe_offset + 6 > len(header) or header[pe_offset:pe_offset + 4] != b"PE\0\0":
    raise UpdateError("ESP kernel has no PE header")
  machine = struct.unpack_from("<H", header, pe_offset + 4)[0]
  if machine != 0xaa64:
    raise UpdateError(f"ESP kernel is for unsupported PE machine 0x{machine:04x}")


def efi_state() -> str:
  return run(["efibootmgr", "-v"]).stdout


def efi_entries(label: str) -> list[str]:
  pattern = re.compile(rf"^Boot([0-9A-Fa-f]{{4}})\*?\s+{re.escape(label)}(?:\s|$)")
  return [match.group(1).upper() for line in efi_state().splitlines() if (match := pattern.match(line))]


def delete_efi_entries(label: str, *, except_entry: str | None = None) -> None:
  for entry in efi_entries(label):
    if entry != except_entry:
      run(["efibootmgr", "-b", entry, "-B"], check=False)


def create_efi_entry(slot: str, *, trial: bool, root: str | None = None) -> str:
  label = EFI_TRIAL_LABEL[slot] if trial else EFI_LABEL[slot]
  args = KERNEL_ARGS.format(root=root or f"PARTLABEL={ROOT_LABEL[slot]}", slot=slot)
  if trial:
    args += " vamos.trial=1"

  before = set(efi_entries(label))
  command = [
    "efibootmgr", "-C", "-d", str(DISK), "-p", str(EFI_PARTITION[slot]),
    "-L", label, "-l", EFI_LOADER, "-u", args,
  ]
  run(command)
  after = efi_entries(label)
  created = next((entry for entry in after if entry not in before), after[-1] if after else None)
  if created is None:
    raise UpdateError(f"failed to create EFI entry {label}")
  return created


def set_boot_order(entries: Sequence[str]) -> None:
  if not entries:
    raise UpdateError("refusing to clear EFI BootOrder")
  run(["efibootmgr", "-o", ",".join(entries)])


def set_boot_next(entry: str) -> None:
  run(["efibootmgr", "-n", entry])


def clear_boot_next() -> None:
  result = run(["efibootmgr", "-N"], check=False)
  if BOOT_NEXT_VARIABLE.exists():
    try:
      BOOT_NEXT_VARIABLE.unlink()
    except OSError as exc:
      detail = result.stdout.strip()
      raise UpdateError(f"failed to clear EFI BootNext: {detail}") from exc
  if BOOT_NEXT_VARIABLE.exists():
    raise UpdateError("EFI BootNext still exists after deletion")


def prepare_trial_boot(current: str, target: str) -> tuple[str, str]:
  stable_entry = create_efi_entry(current, trial=False)
  set_boot_order([stable_entry])
  delete_efi_entries(EFI_LABEL[current], except_entry=stable_entry)
  trial_entry = create_efi_entry(target, trial=True)
  set_boot_next(trial_entry)
  delete_efi_entries(EFI_TRIAL_LABEL[target], except_entry=trial_entry)
  return stable_entry, trial_entry


def commit_boot(current: str, previous: str) -> tuple[str, str]:
  current_entry = create_efi_entry(current, trial=False)
  previous_entry = create_efi_entry(previous, trial=False)
  set_boot_order([current_entry, previous_entry])
  clear_boot_next()
  delete_efi_entries(EFI_LABEL[current], except_entry=current_entry)
  delete_efi_entries(EFI_LABEL[previous], except_entry=previous_entry)
  delete_efi_entries(EFI_TRIAL_LABEL[current])
  return current_entry, previous_entry


def rollback_boot(current: str, failed: str) -> str:
  stable_entry = create_efi_entry(current, trial=False)
  set_boot_order([stable_entry])
  clear_boot_next()
  delete_efi_entries(EFI_LABEL[current], except_entry=stable_entry)
  delete_efi_entries(EFI_TRIAL_LABEL[failed])
  return stable_entry


def verify_layout() -> None:
  for slot in ("a", "b"):
    esp = partition_path(slot, "esp")
    system = partition_path(slot, "system")
    if block_size(esp) != ESP_SIZE:
      raise UpdateError(f"{esp} is not exactly {ESP_SIZE} bytes")
    if block_size(system) != SYSTEM_SIZE:
      raise UpdateError(f"{system} is not exactly {SYSTEM_SIZE} bytes")
  userdata = PARTLABEL_DIR / "userdata"
  if not userdata.exists():
    raise UpdateError("userdata partition does not exist")
  if not os.path.ismount("/data"):
    raise UpdateError("/data is not mounted from persistent userdata")


def activate_staged(*, reboot: bool = False) -> dict:
  if os.geteuid() != 0:
    raise UpdateError("activation must run as root")
  verify_layout()
  state = load_state()
  active = current_slot()
  target = state.get("target_slot")
  if state.get("state") not in ("verified", "ready"):
    raise UpdateError("there is no verified vamOS update to activate")
  if state.get("active_slot") != active or target not in ("a", "b") or target == active:
    raise UpdateError("staged update does not target the inactive slot")

  state.update({"phase": "activating", "progress": 100})
  save_state(state)

  images = state.get("images")
  if not isinstance(images, dict):
    raise UpdateError("staged update has no image metadata")
  if not verify_installed():
    raise UpdateError("staged update failed its final on-device hash check")
  verify_system_contents(partition_path(target, "system"), str(state.get("version", "")))
  verify_esp_contents(partition_path(target, "esp"))

  try:
    rollback_boot(active, target)
    stable_entry, trial_entry = prepare_trial_boot(active, target)
    state.update({
      "state": "ready",
      "phase": "ready",
      "progress": 100,
      "previous_entry": stable_entry,
      "trial_entry": trial_entry,
      "ready_at": int(time.time()),
    })
    save_state(state, "trial-armed")
  except Exception as exc:
    rollback_error = None
    try:
      rollback_boot(active, target)
    except Exception as rollback_exc:
      rollback_error = str(rollback_exc)
    state["state"] = "failed"
    state["phase"] = "failed"
    state["error"] = str(exc)
    if rollback_error is not None:
      state["rollback_error"] = rollback_error
    save_state(state, "activation-failed")
    raise

  log(f"vamOS {state.get('version', 'unspecified')} is ready in slot {target}; next boot is a one-shot trial")
  if reboot:
    log("rebooting into trial slot")
    run(["reboot"], capture=False)
  return state


def install(manifest: Manifest, *, reboot: bool = False, activate: bool = True) -> dict:
  if os.geteuid() != 0:
    raise UpdateError("installation must run as root")
  if reboot and not activate:
    raise UpdateError("--reboot cannot be combined with --defer-activation")
  verify_layout()
  active = current_slot()
  target = "b" if active == "a" else "a"
  # A previous staged update may still have BootNext armed. Restore a single
  # known-good persistent entry before touching either inactive partition.
  rollback_boot(active, target)
  state = {
    "schema": 1,
    "state": "writing",
    "phase": "preparing",
    "progress": 0,
    "version": manifest.version,
    "manifest_source": manifest.source,
    "active_slot": active,
    "target_slot": target,
    "started_at": int(time.time()),
    "images": {image.name: asdict(image) for image in manifest.images},
  }
  save_state(state, "install-started")

  try:
    # Root first and ESP last keeps an interrupted target as far from bootable as possible.
    total_work = 2 * sum(image.size for image in manifest.images)
    completed_work = 0
    for name in ("system", "esp"):
      spec = next(image for image in manifest.images if image.name == name)
      work_before_image = completed_work

      def report_progress(phase: str, image_progress: int) -> None:
        phase_offset = 0 if phase == "writing" else spec.size
        current_work = work_before_image + phase_offset + spec.size * image_progress / 100
        state.update({
          "phase": phase,
          "image": spec.name,
          "image_progress": image_progress,
          "progress": min(98, int(current_work * 98 / total_work)),
        })
        save_state(state)

      write_image(spec, partition_path(target, name), progress_callback=report_progress)
      completed_work += 2 * spec.size

    state.update({"phase": "validating", "progress": 99})
    state.pop("image", None)
    state.pop("image_progress", None)
    save_state(state)
    verify_system_contents(partition_path(target, "system"), manifest.version)
    verify_esp_contents(partition_path(target, "esp"))

    state.update({"state": "verified", "phase": "verified", "progress": 100})
    state["verified_at"] = int(time.time())
    save_state(state, "images-verified")
  except Exception as exc:
    rollback_error = None
    try:
      rollback_boot(active, target)
    except Exception as rollback_exc:
      rollback_error = str(rollback_exc)
    state["state"] = "failed"
    state["phase"] = "failed"
    state["error"] = str(exc)
    if rollback_error is not None:
      state["rollback_error"] = rollback_error
    save_state(state, "install-failed")
    raise

  if activate:
    return activate_staged(reboot=reboot)
  log(f"vamOS {manifest.version} is verified in inactive slot {target}; activation is deferred")
  return state


def verify_installed() -> bool:
  state = load_state()
  target = state.get("target_slot")
  images = state.get("images")
  if target not in ("a", "b") or not isinstance(images, dict):
    raise UpdateError("there is no staged update to verify")
  for name in ("system", "esp"):
    raw = images.get(name)
    if not isinstance(raw, dict):
      raise UpdateError(f"state has no {name} image")
    expected = str(raw["sha256"])
    size = int(raw["size"])
    actual = sha256_file(partition_path(target, name), size)
    if actual != expected:
      log(f"{name}: invalid ({actual}, expected {expected})")
      return False
    log(f"{name}: valid")
  return True


def status() -> dict:
  result: dict = {"state": load_state()}
  with contextlib.suppress(Exception):
    result["active_slot"] = current_slot()
  with contextlib.suppress(Exception):
    result["cmdline"] = cmdline()
  with contextlib.suppress(Exception):
    result["efi"] = efi_state()
  result["layout"] = {}
  for label in ("esp_a", "rootfs_a", "esp_b", "rootfs_b", "userdata"):
    path = PARTLABEL_DIR / label
    result["layout"][label] = {
      "path": str(path.resolve()) if path.exists() else None,
      "size": block_size(path) if path.exists() else None,
    }
  return result


def _layout_json() -> dict:
  output = run(["sfdisk", "--json", str(DISK)]).stdout
  json_start = output.find("{")
  if json_start < 0:
    raise UpdateError("sfdisk did not return a JSON partition table")
  return json.loads(output[json_start:])["partitiontable"]


def initialize_layout(*, confirm: bool) -> None:
  if os.geteuid() != 0:
    raise UpdateError("layout initialization must run as root")
  if not confirm:
    raise UpdateError("layout initialization requires --yes")

  table = _layout_json()
  partitions = table.get("partitions", [])
  labels = [partition.get("name") for partition in partitions]
  if labels[:5] == ["esp_a", "rootfs_a", "esp_b", "rootfs_b", "userdata"]:
    log("A/B layout already exists")
    return
  if len(partitions) != 2 or labels not in (["esp", "rootfs"], ["esp_a", "rootfs_a"]):
    raise UpdateError(f"refusing to migrate unexpected partition layout: {labels}")

  sector_size = int(table["sectorsize"])
  if sector_size != 512:
    raise UpdateError(f"unsupported sector size {sector_size}")
  esp_sectors = ESP_SIZE // sector_size
  system_sectors = SYSTEM_SIZE // sector_size
  p1, p2 = partitions
  if int(p1["size"]) != esp_sectors or int(p2["size"]) != system_sectors:
    raise UpdateError("legacy ESP/rootfs sizes do not match the vamOS layout")

  next_sector = int(p2["start"]) + int(p2["size"])
  p3_start = next_sector
  p4_start = p3_start + esp_sectors
  p5_start = p4_start + system_sectors
  last_lba = int(table["lastlba"])
  if p5_start + 2 * 1024 * 1024 // sector_size >= last_lba:
    raise UpdateError("disk is too small for two vamOS slots and userdata")

  backup_dir = STATE_DIR
  backup_dir.mkdir(parents=True, exist_ok=True)
  backup = backup_dir / f"partition-table-before-ab-{int(time.time())}.sfdisk"
  backup.write_text(run(["sfdisk", "--dump", str(DISK)]).stdout, encoding="utf-8")
  log(f"saved partition-table backup to {backup}")

  # This entry addresses the running root by immutable GPT PARTUUID, so it is
  # valid on either side of the label migration. Select it before touching GPT
  # to eliminate the otherwise unavoidable rename-to-EFI power-loss window.
  migration_entry = create_efi_entry("a", trial=False, root=f"PARTUUID={p2['uuid']}")
  set_boot_order([migration_entry])
  delete_efi_entries(EFI_LABEL["a"], except_entry=migration_entry)

  lines = [
    "label: gpt",
    f"label-id: {table['id']}",
    f"device: {DISK}",
    "unit: sectors",
    f"first-lba: {table['firstlba']}",
    f"last-lba: {table['lastlba']}",
    f"sector-size: {sector_size}",
    "",
    _sfdisk_partition_line(1, p1, "esp_a"),
    _sfdisk_partition_line(2, p2, "rootfs_a"),
    f"{DISK}p3 : start={p3_start}, size={esp_sectors}, type=C12A7328-F81F-11D2-BA4B-00A0C93EC93B, name=\"esp_b\"",
    f"{DISK}p4 : start={p4_start}, size={system_sectors}, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name=\"rootfs_b\"",
    f"{DISK}p5 : start={p5_start}, size={last_lba - p5_start + 1}, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, name=\"userdata\"",
    "",
  ]
  layout = "\n".join(lines)
  completed = subprocess.run(["sfdisk", "--no-reread", "--force", str(DISK)], input=layout, text=True)
  if completed.returncode != 0:
    raise UpdateError("sfdisk failed while creating the A/B layout")
  run(["partx", "-u", str(DISK)], check=False, capture=False)
  run(["partx", "-a", "--nr", "3:5", str(DISK)], check=False, capture=False)
  run(["udevadm", "settle", "--timeout=10"], check=False)

  for number in (3, 4, 5):
    device = Path(f"{DISK}p{number}")
    for _ in range(50):
      if device.exists():
        break
      time.sleep(0.1)
    if not device.exists():
      raise UpdateError(f"{device} did not appear; reboot into slot A and run initialize again")

  run(["mkfs.vfat", "-F", "32", "-n", "VAMOS-B", f"{DISK}p3"], capture=False)
  run(["mkfs.ext4", "-F", "-L", "VAMOS-B", f"{DISK}p4"], capture=False)
  run(["mkfs.ext4", "-F", "-L", "VAMOS-DATA", f"{DISK}p5"], capture=False)

  with tempfile.TemporaryDirectory(prefix="vamos-data-") as mount_dir:
    run(["mount", f"{DISK}p5", mount_dir], capture=False)
    try:
      run(["rsync", "-aHAXx", "--numeric-ids", "/data/", f"{mount_dir}/"], capture=False)
      (Path(mount_dir) / ".vamos-userdata").touch()
      os.sync()
    finally:
      run(["umount", mount_dir], capture=False)

  active_entry = create_efi_entry("a", trial=False)
  set_boot_order([active_entry])
  clear_boot_next()
  delete_efi_entries(EFI_LABEL["a"], except_entry=active_entry)
  log("A/B partitions and persistent userdata are ready; reboot into slot A")


def _sfdisk_partition_line(number: int, partition: dict, name: str) -> str:
  fields = [
    f"start={partition['start']}",
    f"size={partition['size']}",
    f"type={partition['type']}",
  ]
  if partition.get("uuid"):
    fields.append(f"uuid={partition['uuid']}")
  fields.append(f"name=\"{name}\"")
  return f"{DISK}p{number} : " + ", ".join(fields)


def main(argv: Sequence[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description="Install and manage A/B vamOS updates")
  subparsers = parser.add_subparsers(dest="command", required=True)

  install_parser = subparsers.add_parser("install", help="install from a vamOS JSON manifest")
  install_parser.add_argument("manifest")
  install_parser.add_argument("--reboot", action="store_true")
  install_parser.add_argument("--defer-activation", action="store_true")

  local_parser = subparsers.add_parser("local", help="install a directory containing esp.img and system.img")
  local_parser.add_argument("directory", type=Path)
  local_parser.add_argument("--reboot", action="store_true")

  activate_parser = subparsers.add_parser("activate", help="activate the verified inactive slot for a one-shot trial")
  activate_parser.add_argument("--reboot", action="store_true")

  subparsers.add_parser("verify", help="verify the currently staged images")
  subparsers.add_parser("status", help="show slots, EFI state, and update state")

  initialize_parser = subparsers.add_parser("initialize", help="migrate a legacy Dragon disk to A/B")
  initialize_parser.add_argument("--yes", action="store_true")

  args = parser.parse_args(argv)
  try:
    if args.command == "status":
      print(json.dumps(status(), indent=2, sort_keys=True))
      return 0
    with update_lock():
      if args.command == "install":
        install(load_manifest(args.manifest), reboot=args.reboot, activate=not args.defer_activation)
      elif args.command == "local":
        install(manifest_from_directory(args.directory), reboot=args.reboot)
      elif args.command == "activate":
        activate_staged(reboot=args.reboot)
      elif args.command == "verify":
        return 0 if verify_installed() else 1
      elif args.command == "initialize":
        initialize_layout(confirm=args.yes)
    return 0
  except (UpdateError, OSError, urllib.error.URLError, json.JSONDecodeError, lzma.LZMAError) as exc:
    print(f"vamos-update: ERROR: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
  raise SystemExit(main())
