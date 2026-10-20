"""Test the shipped native session against an ordinary host image."""

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.flash.edl_normal import EdlDisk
from tools.flash.edl_session import FirehoseSession
from tools.flash.flash_factory import flash_operations


BINARY = Path(__file__).resolve().parents[1] / "bin/edl-ng-dist/edl-ng"


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.image = self.directory / "disk.img"
        self.image.write_bytes(bytes(range(256)) * (8 * 1024 * 1024 // 256))
        self.command = [str(BINARY), "--memory=Spinor", f"--hostdev-as-target={self.image}"]

    def session(self):
        session = FirehoseSession(self.command)
        self.addCleanup(session.close)
        return session

    def test_one_process_for_writes_reads_and_erase(self):
        session = self.session()
        self.assertEqual(session.geometry["sector_size"], 4096)
        self.assertEqual(session.geometry["sectors"], 2048)
        pid = session.process.pid
        disk = EdlDisk("", "Ufs")
        disk.session = session
        original = self.image.read_bytes()
        disk.write_verified(4096, b"a" * 4096)
        self.assertEqual(disk.read(4100, 100), b"a" * 100)
        self.assertEqual(disk.read(0, 4096), original[:4096])
        payload = self.directory / "large.img"
        payload.write_bytes(b"z" * 1024 * 1024)
        disk.write_file_verified(1024 * 1024, payload)
        disk.erase(3 * 1024 * 1024, 4096)
        self.assertEqual(session.process.pid, pid)
        self.assertIsNone(session.process.poll())
        self.assertEqual(self.image.read_bytes()[4 * 1024 * 1024:], original[4 * 1024 * 1024:])
        session.request("close")
        self.assertEqual(session.process.wait(timeout=5), 0)

    def test_out_of_bounds_stops_session_without_writing(self):
        session = self.session()
        before = hashlib.sha256(self.image.read_bytes()).digest()
        with self.assertRaisesRegex(RuntimeError, "outside storage"):
            session.request("erase", sector=2047, count=2)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            session.request("erase", sector=0, count=1)
        self.assertEqual(hashlib.sha256(self.image.read_bytes()).digest(), before)

    def test_file_size_mismatch_stops_session_without_writing(self):
        session = self.session()
        payload = self.directory / "short.img"
        payload.write_bytes(b"x")
        before = self.image.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "exact sector range"):
            session.request("write", sector=0, count=1, path=str(payload))
        self.assertEqual(self.image.read_bytes(), before)

    def test_verification_rejects_corrupt_readback(self):
        session = self.session()
        disk = EdlDisk("", "Ufs")
        disk.session = session
        payload = self.directory / "payload.img"
        payload.write_bytes(b"v" * 4096)
        request = session.request

        def corrupt(operation, **arguments):
            result = request(operation, **arguments)
            if operation == "read":
                Path(arguments["path"]).write_bytes(b"!" * 4096)
            return result

        session.request = corrupt
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            disk.write_file_verified(0, payload)
        self.assertTrue(session.closed)
        self.assertIsNone(disk.session)

    def test_factory_validates_every_range_before_erasing(self):
        disk = Mock(sector_size=4096)
        disk.session.geometry = {"sectors": 2048}
        operations = [
            ({"offset": 0, "size": 4096, "operation": "erase"}, None),
            ({"offset": 8 * 1024 * 1024, "size": 4096, "operation": "program"}, self.image),
        ]
        with self.assertRaisesRegex(ValueError, "exceeds"):
            flash_operations(disk, 4096, operations)
        disk.erase.assert_not_called()
        disk.write_file_verified.assert_not_called()

    def test_timeout_closes_session(self):
        with self.assertRaises(TimeoutError):
            FirehoseSession([sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.05)

    def test_partial_response_does_not_bypass_timeout(self):
        with self.assertRaises(TimeoutError):
            FirehoseSession([sys.executable, "-c",
                             "import sys,time; sys.stdout.write('{'); sys.stdout.flush(); time.sleep(60)"],
                            timeout=0.05)


if __name__ == "__main__":
    unittest.main()
