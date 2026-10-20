#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import package_ota


class ImagePackagingTest(unittest.TestCase):
  def test_erofs_transfers_without_outer_compression_or_partition_padding(self) -> None:
    with tempfile.TemporaryDirectory() as temporary:
      directory = Path(temporary)
      source = directory / "system.img"
      image = bytearray(4096)
      image[1024:1028] = b"\xe2\xe1\xf5\xe0"
      source.write_bytes(image)
      with mock.patch.object(package_ota, "OUTPUT_DIR", directory):
        result = package_ota.package_image("system", source, "https://images.example/objects")
      artifact = directory / result["url"].rsplit("/", 1)[1]
      self.assertEqual(artifact.read_bytes(), image)
      self.assertEqual(result["compression"], "none")
      self.assertEqual(result["size"], len(image))
      self.assertEqual(result["compressed_sha256"], result["sha256"])


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
