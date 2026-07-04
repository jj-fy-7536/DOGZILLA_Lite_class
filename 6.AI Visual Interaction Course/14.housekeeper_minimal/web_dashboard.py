"""总控网页仪表盘。

在电脑浏览器上打开 http://机器狗IP:8091/ ,可以看到:
- 当前阶段、当前表情、任务文本
- 全流程实时画面(人脸/抓球/找线/巡线各阶段通过 frame_bus 发布)
- 播报与事件日志
- 停止当前任务按钮
"""

from __future__ import annotations

import io
import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

import frame_bus


class StatusBoard:
    def __init__(self, max_messages: int = 160) -> None:
        self._lock = threading.Lock()
        self._stage = "STARTING"
        self._expression = ""
        self._task = ""
        self._messages: deque[dict] = deque(maxlen=max_messages)

    def set_stage(self, stage: str) -> None:
        with self._lock:
            self._stage = stage

    def set_expression(self, expression: str) -> None:
        with self._lock:
            self._expression = expression

    def set_task(self, task: str) -> None:
        with self._lock:
            self._task = task

    def add_message(self, text: str, kind: str = "info") -> None:
        with self._lock:
            self._messages.append(
                {"time": time.strftime("%H:%M:%S"), "kind": kind, "text": text}
            )

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "stage": self._stage,
                "expression": self._expression,
                "task": self._task,
                "messages": list(self._messages),
            }


_PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>智能管家犬</title>
<style>
  body { margin:0; background:#101418; color:#e8edf2; font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }
  header { padding:14px 20px; background:#161c23; display:flex; gap:24px; align-items:baseline; flex-wrap:wrap; }
  header h1 { font-size:18px; margin:0; }
  .badge { background:#223041; border-radius:6px; padding:4px 12px; font-size:14px; }
  .badge b { color:#7fd4ff; }
  .stopbtn { margin-left:auto; border:0; background:#c83f3f; color:#fff; border-radius:6px;
             padding:7px 14px; font-size:14px; font-weight:700; cursor:pointer; }
  .stopbtn:active { transform:translateY(1px); }
  .stopbtn[disabled] { opacity:.55; cursor:not-allowed; }
  main { display:flex; gap:16px; padding:16px 20px; flex-wrap:wrap; }
  .video { flex:2 1 480px; min-width:320px; }
  .video img { width:100%; border-radius:8px; background:#000; display:block; }
  .log { flex:1 1 360px; min-width:320px; max-height:70vh; overflow-y:auto;
         background:#161c23; border-radius:8px; padding:10px 14px; font-size:13px; }
  .log div { padding:6px 0; border-bottom:1px solid #222b35; line-height:1.45; }
  .log .time { color:#5f7285; margin-right:8px; }
  .log .tag { display:inline-block; min-width:58px; margin-right:8px; color:#9db0c2; }
  .log .asr { color:#81d4ff; }
  .log .speak { color:#ffd76e; }
  .log .event { color:#8fd48f; }
  .log .skip { color:#8795a3; }
  .log .info { color:#d8e0e8; }
</style>
</head>
<body>
<header>
  <h1>智能管家犬</h1>
  <span class="badge">阶段 <b id="stage">-</b></span>
  <span class="badge">表情 <b id="expression">-</b></span>
  <span class="badge">任务 <b id="task">-</b></span>
  <button class="stopbtn" id="stopbtn" type="button">停止任务</button>
</header>
<main>
  <div class="video"><img src="/stream.mjpg" alt="live"></div>
  <div class="log" id="log"></div>
</main>
<script>
async function tick() {
  try {
    const res = await fetch('/status.json');
    const data = await res.json();
    document.getElementById('stage').textContent = data.stage || '-';
    document.getElementById('expression').textContent = data.expression || '-';
    document.getElementById('task').textContent = data.task || '-';
    const log = document.getElementById('log');
    const labels = {asr: '我听到', speak: '机器狗说', event: '执行', skip: '忽略', info: '信息'};
    const esc = s => String(s || '').replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
    log.innerHTML = data.messages.slice().reverse().map(m =>
      `<div><span class="time">${esc(m.time)}</span><span class="tag">${labels[m.kind] || esc(m.kind)}</span><span class="${esc(m.kind)}">${esc(m.text)}</span></div>`
    ).join('');
  } catch (e) {}
}
setInterval(tick, 1000);
tick();
document.getElementById('stopbtn').addEventListener('click', async () => {
  const btn = document.getElementById('stopbtn');
  btn.disabled = true;
  try {
    await fetch('/stop', {method: 'POST'});
  } finally {
    setTimeout(() => { btn.disabled = false; }, 800);
    tick();
  }
});
</script>
</body>
</html>"""


def _placeholder_jpeg(stage: str) -> bytes:
    """没有子进程画面时生成的占位图(PIL 默认字体只支持英文)。"""
    try:
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (640, 480), (16, 20, 24))
        draw = ImageDraw.Draw(image)
        draw.text((240, 220), "NO CAMERA FEED", fill=(90, 110, 130))
        draw.text((240, 245), "stage: " + stage, fill=(90, 110, 130))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=70)
        return buffer.getvalue()
    except Exception:
        return b""


class DashboardServer:
    def __init__(self, board: StatusBoard, port: int = 8091, on_stop: Callable[[], None] | None = None) -> None:
        self.board = board
        self.port = port
        self.on_stop = on_stop
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        board = self.board
        on_stop = self.on_stop

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    self._send_bytes(_PAGE.encode("utf-8"), "text/html; charset=utf-8")
                elif self.path == "/status.json":
                    payload = json.dumps(board.snapshot(), ensure_ascii=False)
                    self._send_bytes(payload.encode("utf-8"), "application/json; charset=utf-8")
                elif self.path == "/stream.mjpg":
                    self._stream()
                else:
                    self.send_error(404)

            def do_POST(self):
                if self.path == "/stop":
                    if on_stop is not None:
                        on_stop()
                    payload = json.dumps({"ok": True}, ensure_ascii=False)
                    self._send_bytes(payload.encode("utf-8"), "application/json; charset=utf-8")
                else:
                    self.send_error(404)

            def _send_bytes(self, data: bytes, content_type: str):
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)

            def _stream(self):
                self.send_response(200)
                self.send_header("Cache-Control", "no-cache, private")
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                last_placeholder_at = 0.0
                placeholder = b""
                while True:
                    jpeg, _source = frame_bus.read_latest()
                    if jpeg is None:
                        now = time.time()
                        if now - last_placeholder_at > 1.0 or not placeholder:
                            placeholder = _placeholder_jpeg(board.snapshot()["stage"])
                            last_placeholder_at = now
                        jpeg = placeholder
                    if not jpeg:
                        time.sleep(0.2)
                        continue
                    try:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(("Content-Length: %d\r\n\r\n" % len(jpeg)).encode("ascii"))
                        self.wfile.write(jpeg)
                        self.wfile.write(b"\r\n")
                        time.sleep(0.12)
                    except (BrokenPipeError, ConnectionResetError):
                        break

        ThreadingHTTPServer.allow_reuse_address = True
        self._server = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1)
