"""Sequential requests over one edl-ng Firehose connection."""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import tempfile
import time


class FirehoseSession:
    def __init__(self, command: list[str], timeout: float = 120):
        self.timeout = timeout
        self.privileged = command[0] == "sudo"
        self.log = tempfile.TemporaryFile(mode="w+b")
        try:
            self.process = subprocess.Popen(
                [*command, "session"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self.log, start_new_session=True,
            )
        except BaseException:
            self.log.close()
            raise
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.finished = False
        self.closed = False
        try:
            self.geometry = self._response()
        except BaseException:
            self.close()
            raise

    def _response(self) -> dict:
        deadline = time.monotonic() + self.timeout
        line = b""
        while not line.endswith(b"\n"):
            if not self.selector.select(max(0, deadline - time.monotonic())):
                raise TimeoutError("Firehose session timed out; do not retry an uncertain write")
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                self.log.seek(0)
                raise RuntimeError(f"Firehose session exited:\n{self.log.read().decode(errors='replace')[-4000:]}")
            line += chunk
            if len(line) > 65536:
                raise RuntimeError("Oversized Firehose session response")
        response = json.loads(line)
        if response.get("ok") is not True:
            raise RuntimeError(f"Firehose session failed: {response.get('error', response)}")
        return response

    def request(self, operation: str, **arguments) -> dict:
        if self.finished:
            raise RuntimeError("Firehose session is closed")
        try:
            self.process.stdin.write((json.dumps({"operation": operation, **arguments}) + "\n").encode())
            self.process.stdin.flush()
            response = self._response()
            if operation in ("reset", "close"):
                self.finished = True
            return response
        except BaseException:
            self.finished = True
            self.close()
            raise

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.finished = True
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Each session has its own process group, including sudo's child.
            if self.privileged:
                subprocess.run(["sudo", "-n", "kill", "-KILL", "--", f"-{self.process.pid}"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            else:
                os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=5)
        self.selector.close()
        self.process.stdout.close()
        self.log.close()
