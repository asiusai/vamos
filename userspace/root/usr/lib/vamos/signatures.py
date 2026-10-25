"""ML-DSA-65 release signatures. The public key is the pinned release trust anchor."""
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.mldsa import MLDSA65PrivateKey, MLDSA65PublicKey

SIGNATURE_BYTES = 3309


def verify(payload: bytes, signature: bytes, public_pem: bytes) -> None:
  if len(signature) != SIGNATURE_BYTES:
    raise ValueError("Invalid ML-DSA-65 signature size")
  key = serialization.load_pem_public_key(public_pem)
  if not isinstance(key, MLDSA65PublicKey):
    raise ValueError("Release key must use ML-DSA-65")
  key.verify(signature, payload)


def sign(payload: bytes, private_pem: bytes) -> bytes:
  key = serialization.load_pem_private_key(private_pem, password=None)
  if not isinstance(key, MLDSA65PrivateKey):
    raise ValueError("Release key must use ML-DSA-65")
  return key.sign(payload)


def main() -> None:
  import argparse
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--key", type=Path, required=True)
  parser.add_argument("--public", type=Path, required=True)
  parser.add_argument("manifests", nargs="+", type=Path)
  args = parser.parse_args()
  for path in args.manifests:
    signature = sign(path.read_bytes(), args.key.read_bytes())
    verify(path.read_bytes(), signature, args.public.read_bytes())
    target = Path(str(path) + ".sig")
    temporary = target.with_suffix(".sig.tmp")
    try:
      temporary.write_bytes(signature)
      temporary.replace(target)
    finally:
      temporary.unlink(missing_ok=True)


if __name__ == "__main__":
  main()
