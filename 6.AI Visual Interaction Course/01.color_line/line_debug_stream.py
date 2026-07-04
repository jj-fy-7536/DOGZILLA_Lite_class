#!/usr/bin/env python3
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


BOUNDARY = "frame"
DEFAULT_PORT = 8080
DEFAULT_LINE_COLOR = "black"
DEFAULT_ROI_Y_START = 180

COLOR_HSV = {
    "red": ((0, 70, 72), (7, 255, 255)),
    "green": ((54, 109, 78), (77, 255, 255)),
    "blue": ((92, 100, 62), (121, 255, 255)),
    "yellow": ((26, 100, 91), (32, 255, 255)),
    "black": ((0, 0, 0), (180, 255, 80)),
}


def build_index_html(stream_path="/stream.mjpg", line_color=DEFAULT_LINE_COLOR):
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DOGZILLA Line Debug Stream</title>
  <style>
    body {{
      margin: 0;
      background: #111;
      color: #eee;
      font-family: Arial, sans-serif;
      text-align: center;
    }}
    header {{
      padding: 12px 16px;
      background: #1b1b1b;
      border-bottom: 1px solid #333;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 20px;
      font-weight: 600;
    }}
    p {{
      margin: 0;
      color: #bbb;
      font-size: 14px;
    }}
    img {{
      display: block;
      margin: 16px auto;
      width: min(96vw, 960px);
      height: auto;
      image-rendering: auto;
      border: 1px solid #333;
      background: #000;
    }}
  </style>
</head>
<body>
  <header>
    <h1>DOGZILLA Line Debug Stream</h1>
    <p>Line color: {line_color}. Yellow top line marks the cropped ROI view.</p>
  </header>
  <img src="{stream_path}" alt="line detection debug stream">
</body>
</html>
"""


def format_mjpeg_frame(jpeg_bytes, boundary=BOUNDARY):
    header = (
        f"--{boundary}\r\n"
        "Content-Type: image/jpeg\r\n"
        f"Content-Length: {len(jpeg_bytes)}\r\n\r\n"
    ).encode("ascii")
    return header + jpeg_bytes + b"\r\n"


def encode_rgb_frame_to_jpeg(frame_rgb, jpeg_quality=80):
    import cv2

    frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
    ok, encoded = cv2.imencode(
        ".jpg",
        frame_bgr,
        [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)],
    )
    if not ok:
        return None
    return encoded.tobytes()


def select_line_point(hsv_names, xy_list, line_color):
    for name, xy in zip(hsv_names, xy_list):
        if name == line_color:
            return xy
    return None


class FrameStore:
    def __init__(self):
        self._condition = threading.Condition()
        self._frame = None
        self._status = "starting"
        self._version = 0

    def update(self, frame, status):
        with self._condition:
            self._frame = frame
            self._status = status
            self._version += 1
            self._condition.notify_all()

    def set_status(self, status):
        with self._condition:
            self._status = status
            self._condition.notify_all()

    def snapshot(self):
        with self._condition:
            return self._version, self._frame, self._status

    def wait_for_next(self, last_version, timeout=2.0):
        with self._condition:
            self._condition.wait_for(
                lambda: self._version != last_version and self._frame is not None,
                timeout=timeout,
            )
            return self._version, self._frame, self._status


def make_handler(frame_store, stream_path, line_color):
    class LineDebugHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                body = build_index_html(stream_path, line_color).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return

            if path == "/status":
                version, has_frame, status = self._status_payload()
                body = json.dumps(
                    {"version": version, "has_frame": has_frame, "status": status}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return

            if path == stream_path:
                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    f"multipart/x-mixed-replace; boundary={BOUNDARY}",
                )
                self.send_header("Cache-Control", "no-store")
                self.end_headers()

                last_version = -1
                while True:
                    version, frame, _status = frame_store.wait_for_next(last_version)
                    if frame is None:
                        continue
                    last_version = version
                    try:
                        self.wfile.write(format_mjpeg_frame(frame))
                    except (BrokenPipeError, ConnectionResetError):
                        break
                return

            self.send_error(404, "Not found")

        def _status_payload(self):
            version, frame, status = frame_store.snapshot()
            return version, frame is not None, status

        def log_message(self, fmt, *args):
            print("%s - %s" % (self.address_string(), fmt % args))

    return LineDebugHandler
