"""Provision the device identity once, then keep its filesystem read-only."""

import argparse
import ctypes
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile

PERSIST = Path("/persist")
KEY = PERSIST / "asius/identity.seed"
ROOT_MAGIC = b"ASIUSID\x01"
ROOT_BYTES = 64
ROOT_FILE_BYTES = len(ROOT_MAGIC) + ROOT_BYTES + 32
MARKER = PERSIST / ".provisioned"
READY = Path("/run/vamos-identity-ready")
SIZE = 64 * 1024 * 1024
OFFSET = (20 * 1024 + 512 + 1) * 1024 * 1024


def command(*args):
  return subprocess.run(args, check=True, text=True, capture_output=True).stdout.strip()


def load_key(path):
  with path.open("rb") as source:
    data = source.read(ROOT_FILE_BYTES + 1)
  if len(data) != ROOT_FILE_BYTES or not data.startswith(ROOT_MAGIC):
    raise ValueError("Invalid device root-secret format")
  if not hmac.compare_digest(hashlib.sha256(data[:-32]).digest(), data[-32:]):
    raise ValueError("Device root-secret checksum mismatch")
  return data[len(ROOT_MAGIC):-32]


def encode_key(seed):
  if not isinstance(seed, bytes) or len(seed) != ROOT_BYTES:
    raise ValueError("Device identity requires a 64-byte root secret")
  body = ROOT_MAGIC + seed
  return body + hashlib.sha256(body).digest()


def secure_key():
  # Block until the kernel CSPRNG is ready, including on Python builds without
  # os.getrandom. No persistent key is generated from predictable input.
  libc = ctypes.CDLL(None, use_errno=True)
  libc.getrandom.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint]
  libc.getrandom.restype = ctypes.c_ssize_t
  seed = ctypes.create_string_buffer(ROOT_BYTES)
  if libc.getrandom(seed, ROOT_BYTES, 0) != ROOT_BYTES:
    raise OSError(ctypes.get_errno(), "Secure randomness unavailable")
  return seed.raw


def atomic_write(path, data, uid=0, gid=0):
  fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
  try:
    with os.fdopen(fd, "wb") as output:
      os.fchmod(output.fileno(), 0o600)
      os.fchown(output.fileno(), uid, gid)
      output.write(data)
      output.flush()
      os.fsync(output.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
      os.fsync(directory)
    finally:
      os.close(directory)
  finally:
    Path(temporary).unlink(missing_ok=True)


def provision(uid, gid, replacement=None):
  """The caller holds the system lock and has mounted the verified volume."""
  if KEY.is_symlink() or KEY.parent.is_symlink() or MARKER.is_symlink():
    raise ValueError("Identity paths must not be symlinks")
  if replacement is None and KEY.exists():
    key = load_key(KEY)  # Never replace damaged identity storage.
  elif replacement is None and MARKER.exists():
    raise ValueError("Device identity is missing; explicit replacement is required")
  else:
    key = secure_key() if replacement is None else replacement
    encoded = encode_key(key)
    KEY.parent.mkdir(mode=0o750, exist_ok=True)
    os.chown(KEY.parent, 0, gid)
    atomic_write(KEY, encoded, uid, gid)
  if not MARKER.exists():
    atomic_write(MARKER, b"asius-root-identity-v1\n")
  return key


def identity_device():
  # Resolve on the running OS disk, never select an unrelated disk's label.
  from vamos.update import DISK, disk_partition, _layout_json
  device = disk_partition(6)
  table = _layout_json()
  sector = int(table["sectorsize"])
  matches = [part for part in table["partitions"] if part.get("name") == "persist"]
  if len(matches) != 1:
    raise ValueError(f"Missing dedicated persist partition on {DISK}; factory provisioning is required")
  part = matches[0]
  if Path(part["node"]).resolve() != device.resolve() or part["start"] * sector != OFFSET or part["size"] * sector != SIZE:
    raise ValueError("Unexpected persist partition geometry")
  return device


def mount_identity():
  device = identity_device()
  if os.path.ismount(PERSIST):
    source = command("findmnt", "-n", "-o", "SOURCE", str(PERSIST))
    if Path(source).resolve() != device.resolve():
      raise ValueError("/persist is mounted from the wrong device")
    command("mount", "-o", "remount,ro", str(PERSIST))
    return
  PERSIST.mkdir(exist_ok=True)
  probe = subprocess.run(["blkid", "-p", "-o", "export", str(device)], text=True, capture_output=True)
  fields = dict(line.split("=", 1) for line in probe.stdout.splitlines() if "=" in line)
  if "TYPE" not in fields:
    # Only an entirely erased range is eligible for first-boot formatting.
    with device.open("rb", buffering=0) as stream:
      for _ in range(SIZE // (1024 * 1024)):
        data = stream.read(1024 * 1024)
        if len(data) != 1024 * 1024 or any(data):
          raise ValueError("Identity storage is not empty; refusing to format it")
    command("mkfs.ext4", "-F", "-m", "0", "-L", "VAMOS-IDENTITY", str(device))
  elif fields.get("TYPE") != "ext4" or fields.get("LABEL") != "VAMOS-IDENTITY":
    raise ValueError("Unexpected identity filesystem; refusing to format it")
  # Journal recovery occurs here if the previous provisioning lost power.
  command("mount", "-t", "ext4", "-o", "ro,nosuid,nodev,noexec", str(device), str(PERSIST))


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("action", choices=("initialize", "status", "replace"))
  parser.add_argument("--from-file", type=Path, help="Import an existing Asius root-secret file instead of generating one")
  parser.add_argument("--confirm-identity-change", action="store_true",
                      help="Accept a new device identity and loss of access through the previous identity")
  args = parser.parse_args()
  if os.geteuid() != 0:
    parser.error("Run as root")
  if args.action != "replace" and (args.from_file or args.confirm_identity_change):
    parser.error("Replacement options require replace")
  if args.action == "replace":
    if not args.confirm_identity_change:
      parser.error("Replacement requires --confirm-identity-change")
    processes = Path("/sys/fs/cgroup/openpilot/cgroup.procs")
    if processes.exists() and processes.read_text().strip():
      parser.error("Stop openpilot before replacing its identity")
    if subprocess.run(["sv", "status", "/var/service/openpilot"], capture_output=True).stdout.startswith(b"run:"):
      parser.error("Stop the openpilot service before replacing its identity")
  lock = os.open("/run/vamos-identity.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
  with os.fdopen(lock, "w"):
    fcntl.flock(lock, fcntl.LOCK_EX)
    mount_identity()
    if args.action == "status":
      load_key(KEY)
      print(json.dumps({"key": str(KEY), "mount": command("findmnt", "-n", "-o", "OPTIONS", str(PERSIST)),
                        "ready": READY.exists()}))
      return 0
    READY.unlink(missing_ok=True)
    replacement = (load_key(args.from_file) if args.from_file else secure_key()) if args.action == "replace" else None
    account = pwd.getpwnam("comma")
    try:
      # Normal boots only read the key. Remount only for first provisioning or
      # an explicit replacement. The single private file is the source of truth.
      if replacement is not None or not KEY.exists() or not MARKER.exists():
        command("mount", "-o", "remount,rw", str(PERSIST))
        provision(account.pw_uid, account.pw_gid, replacement)
      else:
        load_key(KEY)
    finally:
      command("mount", "-o", "remount,ro", str(PERSIST))
    if args.action == "replace":
      # Keep old user data intact. Require reboot so every process reloads the
      # identity. Pairing to a new identity is an explicit new enrollment.
      print("Identity replaced. Reboot before starting openpilot.")
    else:
      READY.touch(mode=0o600)
    return 0


if __name__ == "__main__":
  try:
    raise SystemExit(main())
  except (OSError, ValueError, subprocess.CalledProcessError) as error:
    raise SystemExit(f"Device identity unavailable: {error}") from error
