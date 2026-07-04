#!/home/pi/RaspberryPi-CM5/xgovenv/bin/python
# -*- coding: utf-8 -*-
"""
智能管家犬 - 人脸识别待命模块

按《智能管家犬需求文档》阶段 1 实现:
- 低帧率检测前方人脸
- 有主人人脸库时判断主人/陌生人
- 浏览器实时画面显示检测框

主人人脸库目录:
    /home/pi/RaspberryPi-CM5/car/faces/owner/*.jpg

电脑实时画面:
    http://机器狗IP:8090/
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

try:
    import frame_bus
except ImportError:
    frame_bus = None

ROBOT_PATHS = ("/home/pi/RaspberryPi-CM5/app", "/home/pi/RaspberryPi-CM5/demos")
for robot_path in ROBOT_PATHS:
    if robot_path not in sys.path:
        sys.path.append(robot_path)

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None


FRAME_WIDTH = 640
FRAME_HEIGHT = 480
FACE_MIN_SIZE = (80, 80)
FACE_MAX_CENTER_Y_RATIO = 0.78
IDENTITY_HISTORY_SIZE = 5
OWNER_CONFIRM_COUNT = 2
STRANGER_CONFIRM_COUNT = 4


def kill_official_services() -> None:
    for pattern in ("python main.py", "flacksocket/app.py", "camera_server.py"):
        subprocess.run(["pkill", "-f", pattern], check=False)
    time.sleep(0.5)


@dataclass
class FaceResult:
    x: int
    y: int
    w: int
    h: int
    identity: str
    score: float


class Camera:
    def __init__(self) -> None:
        if Picamera2 is None:
            raise RuntimeError("未检测到 picamera2，请在机器狗 xgovenv 环境运行")
        self.cam = Picamera2()
        config = self.cam.create_video_configuration(
            main={"format": "BGR888", "size": (FRAME_WIDTH, FRAME_HEIGHT)},
            buffer_count=2,
        )
        self.cam.configure(config)
        self.cam.start()
        time.sleep(0.8)

    def capture_bgr(self) -> np.ndarray:
        frame = self.cam.capture_array()
        if frame.ndim == 3 and frame.shape[2] == 3:
            return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    def close(self) -> None:
        self.cam.stop()


class FaceDetector:
    def __init__(self) -> None:
        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        if not cascade_path.exists():
            cascade_path = Path("/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml")
        self.cascade = cv2.CascadeClassifier(str(cascade_path))
        if self.cascade.empty():
            raise RuntimeError("无法加载 OpenCV 人脸检测模型")

    def detect(self, frame_bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        faces = self.cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=8,
            minSize=FACE_MIN_SIZE,
        )
        valid_faces = []
        for face in faces:
            x, y, w, h = tuple(map(int, face))
            center_y = y + h / 2
            if center_y > FRAME_HEIGHT * FACE_MAX_CENTER_Y_RATIO:
                continue
            valid_faces.append((x, y, w, h))
        return sorted(valid_faces, key=lambda f: f[2] * f[3], reverse=True)


class FaceRecognizer:
    def __init__(self, faces_dir: Path, threshold: float) -> None:
        self.faces_dir = faces_dir
        self.owner_dir = faces_dir / "owner"
        self.threshold = threshold
        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        if not cascade_path.exists():
            cascade_path = Path("/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml")
        self.cascade = cv2.CascadeClassifier(str(cascade_path))
        self.templates: list[np.ndarray] = []
        self.load()

    def load(self) -> None:
        self.owner_dir.mkdir(parents=True, exist_ok=True)
        self.templates = []
        for image_path in sorted(self.owner_dir.glob("*")):
            if image_path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            image = cv2.imread(str(image_path))
            if image is None:
                continue
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            face_gray = self._crop_largest_face(gray)
            self.templates.append(self._normalize(face_gray))
        if self.templates:
            print("已加载主人人脸样本: {} 张".format(len(self.templates)))
        else:
            print("未找到主人人脸库，将只做人脸检测: {}".format(self.owner_dir))

    def save_owner_sample(self, frame_bgr: np.ndarray, face: tuple[int, int, int, int]) -> Path:
        x, y, w, h = face
        pad = int(max(w, h) * 0.18)
        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(frame_bgr.shape[1], x + w + pad)
        y2 = min(frame_bgr.shape[0], y + h + pad)
        crop = frame_bgr[y1:y2, x1:x2]
        image_path = self.owner_dir / "owner_{}.jpg".format(time.strftime("%Y%m%d_%H%M%S"))
        cv2.imwrite(str(image_path), crop)
        self.load()
        return image_path

    def identify(self, face_gray: np.ndarray) -> tuple[str, float]:
        if not self.templates:
            return "face", 0.0
        sample = self._normalize(face_gray)
        best_diff = min(float(np.mean(cv2.absdiff(sample, template))) for template in self.templates)
        score = max(0.0, min(1.0, 1.0 - best_diff / 80.0))
        if score >= self.threshold:
            return "owner", score
        return "stranger", score

    @staticmethod
    def _normalize(gray: np.ndarray) -> np.ndarray:
        resized = cv2.resize(gray, (100, 100), interpolation=cv2.INTER_AREA)
        return cv2.equalizeHist(resized)

    def _crop_largest_face(self, gray: np.ndarray) -> np.ndarray:
        faces = self.cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(50, 50),
        )
        if len(faces) == 0:
            return gray
        x, y, w, h = max([tuple(map(int, face)) for face in faces], key=lambda f: f[2] * f[3])
        return gray[y : y + h, x : x + w]


class VideoStreamer:
    def __init__(self, port: int, capture_owner: Callable[[], str] | None = None) -> None:
        self.port = port
        self.capture_owner = capture_owner
        self._jpeg: bytes | None = None
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        streamer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    html = (
                        "<html><head><title>DOGZILLA Face</title></head>"
                        "<body style='margin:0;background:#111;color:#eee;font-family:sans-serif;'>"
                        "<div style='padding:10px;display:flex;gap:12px;align-items:center;'>"
                        "<span>DOGZILLA Face Interaction</span>"
                        "<button onclick=\"fetch('/capture-owner',{method:'POST'}).then(r=>r.text()).then(t=>document.getElementById('msg').textContent=t)\" "
                        "style='font-size:16px;padding:6px 12px;'>保存主人脸</button>"
                        "<span id='msg' style='color:#ffd400;'></span>"
                        "</div>"
                        "<img src='/stream.mjpg' style='width:100%;max-width:960px;display:block;'>"
                        "</body></html>"
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(html)))
                    self.end_headers()
                    self.wfile.write(html)
                    return
                if self.path != "/stream.mjpg":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                while True:
                    with streamer._lock:
                        jpeg = streamer._jpeg
                    if jpeg is None:
                        time.sleep(0.05)
                        continue
                    try:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(("Content-Length: %d\r\n\r\n" % len(jpeg)).encode("ascii"))
                        self.wfile.write(jpeg)
                        self.wfile.write(b"\r\n")
                        time.sleep(0.08)
                    except (BrokenPipeError, ConnectionResetError):
                        break

            def do_POST(self):
                if self.path != "/capture-owner":
                    self.send_error(404)
                    return
                if streamer.capture_owner is None:
                    message = "capture callback missing"
                else:
                    message = streamer.capture_owner()
                data = message.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        ThreadingHTTPServer.allow_reuse_address = True
        self._server = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def update(self, frame_bgr: np.ndarray) -> None:
        ok, jpeg = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if ok:
            with self._lock:
                self._jpeg = jpeg.tobytes()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1)


def annotate(frame_bgr: np.ndarray, results: list[FaceResult], status: str) -> np.ndarray:
    out = frame_bgr.copy()
    for result in results:
        if result.identity == "owner":
            color = (0, 255, 0)
            label = "OWNER {:.2f}".format(result.score)
        elif result.identity == "stranger":
            color = (0, 0, 255)
            label = "STRANGER {:.2f}".format(result.score)
        else:
            color = (0, 255, 255)
            label = "FACE"
        cv2.rectangle(out, (result.x, result.y), (result.x + result.w, result.y + result.h), color, 2)
        cv2.putText(out, label, (result.x, max(20, result.y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    cv2.putText(out, status, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return out


def write_auth_result(result_path: Path, identity: str, score: float) -> None:
    result_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "identity": identity,
        "score": round(float(score), 4),
        "timestamp": time.strftime("%Y%m%d_%H%M%S"),
    }
    tmp = result_path.with_name(result_path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, result_path)


def run(args: argparse.Namespace) -> None:
    kill_official_services()
    camera = Camera()
    detector = FaceDetector()
    recognizer = FaceRecognizer(Path(args.faces_dir), args.threshold)
    latest_lock = threading.Lock()
    latest_frame: np.ndarray | None = None
    latest_faces: list[tuple[int, int, int, int]] = []

    def capture_owner() -> str:
        nonlocal latest_frame, latest_faces
        with latest_lock:
            frame = None if latest_frame is None else latest_frame.copy()
            faces = list(latest_faces)
        if frame is None or not faces:
            return "没有检测到人脸"
        image_path = recognizer.save_owner_sample(frame, faces[0])
        return "已保存主人脸: {}".format(image_path.name)

    streamer = VideoStreamer(args.port, capture_owner)
    streamer.start()
    print("电脑实时画面: http://{}:{}/".format(args.robot_ip, args.port))

    last_state = "idle"
    last_notify_at = 0.0
    identity_history: deque[str] = deque(maxlen=IDENTITY_HISTORY_SIZE)
    try:
        while True:
            frame = camera.capture_bgr()
            faces = detector.detect(frame)
            with latest_lock:
                latest_frame = frame.copy()
                latest_faces = list(faces)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            results: list[FaceResult] = []
            for x, y, w, h in faces[:3]:
                identity, score = recognizer.identify(gray[y : y + h, x : x + w])
                results.append(FaceResult(x=x, y=y, w=w, h=h, identity=identity, score=score))

            raw_identity = results[0].identity if results else "none"
            identity_history.append(raw_identity)
            identity_counts = Counter(identity_history)

            stable_identity = "none"
            if identity_counts["owner"] >= OWNER_CONFIRM_COUNT:
                stable_identity = "owner"
            elif identity_counts["stranger"] >= STRANGER_CONFIRM_COUNT:
                stable_identity = "stranger"
            elif identity_counts["face"] >= OWNER_CONFIRM_COUNT:
                stable_identity = "face"

            state = "waiting"
            if stable_identity == "owner":
                state = "owner: 主人你好"
            elif stable_identity == "stranger":
                state = "stranger: 你是谁"
            elif stable_identity == "face":
                state = "face detected"
            elif results:
                state = "checking"

            if results:
                if stable_identity in ("owner", "stranger", "face"):
                    results[0].identity = stable_identity
                else:
                    results[0].identity = "face"

            now = time.time()
            if state != last_state or now - last_notify_at > 5:
                print(state)
                last_state = state
                last_notify_at = now

            annotated = annotate(frame, results, state)
            streamer.update(annotated)
            if frame_bus is not None:
                frame_bus.publish_bgr(annotated, "face")

            # 结构化握手:确认主人后写结果文件并主动退出(exit 0),
            # 摄像头在 finally 中确定释放,总控不再靠 grep 日志判断
            if args.exit_on_owner and stable_identity == "owner":
                score = results[0].score if results else 0.0
                write_auth_result(Path(args.auth_result), "owner", score)
                print("owner confirmed, exiting for handoff")
                return

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n关闭人脸识别模块")
    finally:
        streamer.stop()
        camera.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="智能管家犬人脸识别待命模块")
    parser.add_argument("--faces-dir", default="/home/pi/RaspberryPi-CM5/car/faces")
    parser.add_argument("--threshold", type=float, default=0.40)
    parser.add_argument("--interval", type=float, default=0.15)
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--robot-ip", default="172.20.10.4")
    parser.add_argument(
        "--exit-on-owner",
        action="store_true",
        help="确认主人后写结果文件并退出(exit 0),供总控做结构化握手",
    )
    parser.add_argument(
        "--auth-result",
        default="/home/pi/xgoPictures/housekeeper/auth_result.json",
        help="--exit-on-owner 时的认证结果文件路径",
    )
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
