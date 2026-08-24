#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import package_ota


class ManifestSigningTest(unittest.TestCase):
  def test_ed25519_signature_is_accepted_by_openssl(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      directory = Path(temporary)
      private_key = directory / "private.pem"
      public_key = directory / "public.pem"
      manifest = directory / "vamos.json"
      manifest.write_bytes(b'{"version":"test"}\n')
      subprocess.run(
        ["openssl", "genpkey", "-algorithm", "Ed25519", "-out", private_key],
        check=True,
      )
      subprocess.run(
        ["openssl", "pkey", "-in", private_key, "-pubout", "-out", public_key],
        check=True,
      )

      signature = package_ota.sign_manifest(manifest, private_key)

      self.assertEqual(signature.stat().st_size, 64)
      subprocess.run([
        "openssl", "pkeyutl", "-verify", "-pubin", "-inkey", public_key,
        "-rawin", "-in", manifest, "-sigfile", signature,
      ], check=True)


if __name__ == "__main__":
  unittest.main()
