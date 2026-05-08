#!/usr/bin/env python3
"""Serve Dragon road and wide camera VisionIPC streams as MJPEG."""

from __future__ import annotations

import argparse
import io
import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np
from PIL import Image
from msgq.visionipc import VisionIpcClient, VisionStreamType


CAMERAS = {
  "road": VisionStreamType.VISION_STREAM_ROAD,
  "wide": VisionStreamType.VISION_STREAM_WIDE_ROAD,
}


def nv12_to_ycbcr(buf) -> Image.Image:
  uv_height = ((buf.height // 2) + 15) // 16 * 16
  uv_plane_size = buf.stride * uv_height

  y = Image.frombuffer("L", (buf.width, buf.height), buf.data[:buf.uv_offset], "raw", "L", buf.stride, 1)
  uv_data = buf.data[buf.uv_offset:buf.uv_offset + uv_plane_size]
  uv = np.frombuffer(uv_data, dtype=np.uint8).reshape((-1, buf.stride))[:buf.height // 2, :buf.width]

  cb = Image.fromarray(uv[:, 0::2]).resize((buf.width, buf.height), Image.Resampling.BILINEAR)
  cr = Image.fromarray(uv[:, 1::2]).resize((buf.width, buf.height), Image.Resampling.BILINEAR)
  return Image.merge("YCbCr", (y, cb, cr))


class CameraWorker:
  def __init__(self, name: str, stream_type: VisionStreamType, max_fps: float, quality: int, max_width: int | None):
    self.name = name
    self.stream_type = stream_type
    self.max_fps = max_fps
    self.quality = quality
    self.max_width = max_width
    self.cond = threading.Condition()
    self.jpeg: bytes | None = None
    self.frame_count = 0
    self.last_frame_time = 0.0
    self.last_error: str | None = None
    self.width = 0
    self.height = 0

  def start(self) -> None:
    threading.Thread(target=self._run, name=f"{self.name}-camera", daemon=True).start()

  def _run(self) -> None:
    min_interval = 1.0 / self.max_fps if self.max_fps > 0 else 0.0
    client = VisionIpcClient("camerad", self.stream_type, True)
    while True:
      try:
        client.connect(True)
        while True:
          buf = client.recv()
          now = time.monotonic()
          if min_interval and now - self.last_frame_time < min_interval:
            continue

          img = nv12_to_ycbcr(buf)
          self.width, self.height = img.size
          if self.max_width and img.width > self.max_width:
            new_height = int(img.height * self.max_width / img.width)
            img = img.resize((self.max_width, new_height), Image.Resampling.BILINEAR)

          out = io.BytesIO()
          img.save(out, "JPEG", quality=self.quality, optimize=False)
          with self.cond:
            self.jpeg = out.getvalue()
            self.frame_count += 1
            self.last_frame_time = now
            self.last_error = None
            self.cond.notify_all()
      except Exception as e:
        with self.cond:
          self.last_error = repr(e)
          self.cond.notify_all()
        time.sleep(1.0)


class CameraServer(ThreadingHTTPServer):
  def __init__(self, server_address, workers: dict[str, CameraWorker]):
    super().__init__(server_address, Handler)
    self.workers = workers
    self.started_at = time.monotonic()


class Handler(BaseHTTPRequestHandler):
  server: CameraServer

  def do_GET(self) -> None:
    parsed = urlparse(self.path)
    if parsed.path == "/":
      self._html()
      return
    if parsed.path == "/status.json":
      self._status()
      return
    if parsed.path.startswith("/snapshot/"):
      name = parsed.path.removeprefix("/snapshot/").removesuffix(".jpg")
      self._snapshot(name)
      return
    if parsed.path.startswith("/stream/"):
      name = parsed.path.removeprefix("/stream/").removesuffix(".mjpg")
      params = parse_qs(parsed.query)
      fps = float(params.get("fps", [0])[0] or 0)
      self._stream(name, fps)
      return
    self.send_error(HTTPStatus.NOT_FOUND)

  def log_message(self, fmt, *args) -> None:
    print(f"{self.address_string()} - {fmt % args}", flush=True)

  def _html(self) -> None:
    body = b"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Asius Camera Calibration</title>
  <style>
    body { margin: 0; font-family: system-ui, sans-serif; background: #111; color: #eee; }
    header { padding: 10px 14px; display: flex; gap: 16px; align-items: center; background: #1b1b1b; }
    header a { color: #9bd1ff; }
    main { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; padding: 8px; }
    section { min-width: 0; background: #000; }
    h2 { font-size: 14px; font-weight: 600; margin: 0; padding: 8px 10px; background: #222; }
    img { display: block; width: 100%; height: auto; image-rendering: auto; }
    @media (max-width: 900px) { main { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <header>
    <strong>Asius Camera Calibration</strong>
    <a href="/snapshot/road.jpg">road jpg</a>
    <a href="/snapshot/wide.jpg">wide jpg</a>
    <a href="/status.json">status</a>
  </header>
  <main>
    <section><h2>Road</h2><img src="/stream/road.mjpg"></section>
    <section><h2>Wide</h2><img src="/stream/wide.mjpg"></section>
  </main>
</body>
</html>
"""
    self.send_response(HTTPStatus.OK)
    self.send_header("Content-Type", "text/html; charset=utf-8")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def _status(self) -> None:
    uptime = time.monotonic() - self.server.started_at
    data = {
      name: {
        "frames": worker.frame_count,
        "source_width": worker.width,
        "source_height": worker.height,
        "last_error": worker.last_error,
        "age_s": None if worker.last_frame_time == 0 else time.monotonic() - worker.last_frame_time,
        "fps_since_start": worker.frame_count / uptime if uptime > 0 else 0,
      }
      for name, worker in self.server.workers.items()
    }
    body = json.dumps(data, indent=2).encode()
    self.send_response(HTTPStatus.OK)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def _snapshot(self, name: str) -> None:
    worker = self.server.workers.get(name)
    if worker is None:
      self.send_error(HTTPStatus.NOT_FOUND)
      return
    jpeg = self._wait_for_jpeg(worker)
    if jpeg is None:
      self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, worker.last_error or "no frame")
      return
    self.send_response(HTTPStatus.OK)
    self.send_header("Content-Type", "image/jpeg")
    self.send_header("Cache-Control", "no-store")
    self.send_header("Content-Length", str(len(jpeg)))
    self.end_headers()
    self.wfile.write(jpeg)

  def _stream(self, name: str, fps: float) -> None:
    worker = self.server.workers.get(name)
    if worker is None:
      self.send_error(HTTPStatus.NOT_FOUND)
      return
    self.send_response(HTTPStatus.OK)
    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
    self.send_header("Cache-Control", "no-store")
    self.end_headers()

    last_seen = -1
    min_interval = 1.0 / fps if fps > 0 else 0.0
    while True:
      with worker.cond:
        worker.cond.wait_for(lambda: worker.frame_count != last_seen or worker.last_error, timeout=5.0)
        if worker.jpeg is None:
          continue
        last_seen = worker.frame_count
        jpeg = worker.jpeg
      try:
        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ")
        self.wfile.write(str(len(jpeg)).encode())
        self.wfile.write(b"\r\n\r\n")
        self.wfile.write(jpeg)
        self.wfile.write(b"\r\n")
        self.wfile.flush()
        if min_interval:
          time.sleep(min_interval)
      except (BrokenPipeError, ConnectionResetError):
        return

  @staticmethod
  def _wait_for_jpeg(worker: CameraWorker, timeout: float = 5.0) -> bytes | None:
    deadline = time.monotonic() + timeout
    with worker.cond:
      while worker.jpeg is None and time.monotonic() < deadline:
        worker.cond.wait(timeout=0.25)
      return worker.jpeg


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("--host", default="0.0.0.0")
  parser.add_argument("--port", type=int, default=8088)
  parser.add_argument("--fps", type=float, default=20.0)
  parser.add_argument("--quality", type=int, default=70)
  parser.add_argument("--max-width", type=int, default=0, help="0 keeps full resolution")
  args = parser.parse_args()

  workers = {
    name: CameraWorker(name, stream, args.fps, args.quality, args.max_width or None)
    for name, stream in CAMERAS.items()
  }
  for worker in workers.values():
    worker.start()

  print(f"Serving road + wide camera streams on http://{args.host}:{args.port}", flush=True)
  CameraServer((args.host, args.port), workers).serve_forever()


if __name__ == "__main__":
  main()
