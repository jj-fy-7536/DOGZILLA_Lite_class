#!/home/pi/RaspberryPi-CM5/xgovenv/bin/python
# -*- coding: utf-8 -*-
"""
红球识别 - 靠近 - 抓取 一体化脚本

基于以下已有资料整合而成:
- 红球识别移动抓取流程.md (标定窗口/机械臂参数来源)
- DOGZILLA_Lite_二次开发教程与搜救犬方案.md (环境/坑点/已验证API)

【重要】本脚本中控制机器狗前进/后退/横移/转身使用的是
dog.move("x"/"y", speed) 和 dog.turn(speed)，
这是 XGO 系列常见接口写法，但在你提供的文档中【未被明确验证过】。
首次使用前请务必先用 --test-move 单独测试移动/转身是否正常，
不要直接跑 --mode full 上真实抓取流程。

使用方法:
    python ball_grab.py --help

【修复记录】
#1 修复"蹲下不触发"：一发现球就蹲下，不再用面积阈值。
#2 修复"走到球前不抓"：渐进减速 + 球消失时盲抓。
#3 修复"盲抓仍然不触发"：用峰值面积(历史最大值)判断盲抓，
   而非最后一帧面积——球掉出画面前只能看到半个球，最后几帧
   面积是下降的，用最后一帧的值永远够不到阈值。
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

# 管家犬总控的共享画面总线(独立运行本脚本时不存在则静默跳过)
sys.path.append(str(Path(__file__).resolve().parent.parent / "14.housekeeper_minimal"))
try:
    import frame_bus
except ImportError:
    frame_bus = None

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None

try:
    from xgolib import XGO
except ImportError:
    XGO = None


# ---------------------- 基础配置 ---------------------- #

FRAME_WIDTH = 640
FRAME_HEIGHT = 480
FRAME_CENTER_X = FRAME_WIDTH // 2

OUTPUT_DIR = "/home/pi/xgoPictures/ball_grab"

DEFAULT_CONFIG = dict(
    target_color="red",
    center_tolerance=55,
    ready_area_min=0.028,      # 稍微提前抓，避免球贴到狗脚下后触发盲抓
    ready_area_max=0.040,
    approach_speed=10,
    approach_seconds=0.8,
    align_speed=6,
    align_seconds=0.55,
    max_steps=200,
    confirm_frames=1,
    lost_target_limit=6,

    # ---- 搜寻 ----
    search_turn_speed=8,
    search_turn_seconds=0.4,
    max_search_no_detect=40,

    # ---- 盲抓 (作为兜底) ----
    blind_grab_area=0.035,
    blind_grab_lost_frames=2,

    # ---- 渐进减速 ----
    fine_approach_area=0.020,  # 0.020 开始减速
)

COLOR_HSV_RANGES = {
    "red": [
        ((0, 100, 80), (8, 255, 255)),
        ((172, 100, 80), (180, 255, 255)),
    ],
    "green": [((35, 43, 46), (77, 255, 255))],
    "blue": [((100, 43, 46), (124, 255, 255))],
    "yellow": [((26, 43, 46), (34, 255, 255))],
}

COLOR_LABELS = {
    "red": "红球",
    "green": "绿球",
    "blue": "蓝球",
    "yellow": "黄球",
}


def kill_official_services():
    for pattern in ("python main.py", "flacksocket/app.py", "camera_server.py"):
        subprocess.run(["pkill", "-f", pattern], check=False)
    time.sleep(1)


# ---------------------- 摄像头 ---------------------- #

class Camera:
    def __init__(self):
        if Picamera2 is None:
            raise RuntimeError("未检测到 picamera2，请确认这是在机器狗上通过 xgovenv 运行")
        self.cam = Picamera2()
        config = self.cam.create_video_configuration(
            main={"format": "RGB888", "size": (FRAME_WIDTH, FRAME_HEIGHT)},
            buffer_count=2,
        )
        self.cam.configure(config)
        self.cam.start()

        self._lock = threading.Lock()
        self._latest_frame = None
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

        for _ in range(50):
            if self._latest_frame is not None:
                break
            time.sleep(0.05)
        if self._latest_frame is None:
            raise RuntimeError("摄像头启动超时，未采集到任何画面")

    def _capture_loop(self):
        while self._running:
            frame = self.cam.capture_array()
            with self._lock:
                self._latest_frame = frame

    def capture_bgr(self):
        with self._lock:
            if self._latest_frame is None:
                raise RuntimeError("摄像头还没有采集到画面")
            return self._latest_frame.copy()

    def close(self):
        self._running = False
        self._thread.join(timeout=2)
        self.cam.stop()


# ---------------------- 颜色球检测 ---------------------- #

DETECT_SCALE = 0.5


def color_ranges_for(target_color):
    target_color = str(target_color or "red").strip().lower()
    return COLOR_HSV_RANGES.get(target_color, COLOR_HSV_RANGES["red"])


def color_label(target_color):
    target_color = str(target_color or "red").strip().lower()
    return COLOR_LABELS.get(target_color, COLOR_LABELS["red"])


def detect_colored_ball(frame_bgr, target_color="red", scale=DETECT_SCALE):
    small = cv2.resize(frame_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)

    mask = None
    for lower, upper in color_ranges_for(target_color):
        current = cv2.inRange(hsv, np.array(lower), np.array(upper))
        mask = current if mask is None else cv2.bitwise_or(mask, current)

    mask = cv2.erode(mask, None, iterations=2)
    mask = cv2.dilate(mask, None, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    largest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(largest)
    if area < 80 * scale * scale:
        return None

    (x, y), radius = cv2.minEnclosingCircle(largest)
    circle_area = np.pi * radius * radius
    circularity = area / circle_area if circle_area > 0 else 0

    small_area = small.shape[0] * small.shape[1]
    area_ratio = area / small_area

    return {
        "cx": float(x / scale),
        "cy": float(y / scale),
        "radius": float(radius / scale),
        "area_ratio": float(area_ratio),
        "confidence": float(min(circularity, 1.0)),
    }


def detect_red_ball(frame_bgr, scale=DETECT_SCALE):
    return detect_colored_ball(frame_bgr, "red", scale)


def annotate_frame(frame_bgr, detection, status_text=""):
    out = frame_bgr.copy()
    h, w = out.shape[:2]
    cv2.line(out, (w // 2, 0), (w // 2, h), (0, 255, 255), 1)
    if detection:
        cx, cy, r = int(detection["cx"]), int(detection["cy"]), int(detection["radius"])
        cv2.circle(out, (cx, cy), r, (0, 255, 0), 2)
        cv2.circle(out, (cx, cy), 3, (0, 0, 255), -1)
        label = "area={:.3f} conf={:.2f}".format(detection["area_ratio"], detection["confidence"])
        cv2.putText(out, label, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    if status_text:
        cv2.putText(out, status_text, (10, h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)
    return out


# ---------------------- 电脑实时图传 ---------------------- #

class VideoStreamer:
    def __init__(self, port=8089):
        self.port = port
        self._lock = threading.Lock()
        self._jpeg = None
        self._server = None
        self._thread = None

    def start(self):
        streamer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    html = (
                        "<html><head><title>DOGZILLA Live</title></head>"
                        "<body style='margin:0;background:#111;color:#eee;font-family:sans-serif;'>"
                        "<div style='padding:10px;'>DOGZILLA Live Preview</div>"
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
                self.send_header("Age", "0")
                self.send_header("Cache-Control", "no-cache, private")
                self.send_header("Pragma", "no-cache")
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
                        time.sleep(0.05)
                    except (BrokenPipeError, ConnectionResetError):
                        break

        ThreadingHTTPServer.allow_reuse_address = True
        self._server = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        print("电脑实时画面: http://172.20.10.4:{}/".format(self.port))

    def update(self, frame_bgr):
        ok, jpeg = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            return
        with self._lock:
            self._jpeg = jpeg.tobytes()

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1)


# ---------------------- 机器狗动作封装 ---------------------- #

class DogController:
    def __init__(self):
        if XGO is None:
            raise RuntimeError("未检测到 xgolib，请确认这是在机器狗上通过 xgovenv 运行")
        self.dog = XGO(port="/dev/ttyAMA0", version="xgolite")

    def move_forward(self, speed, seconds):
        self.dog.move("x", speed)
        time.sleep(seconds)
        self.dog.move("x", 0)

    def move_backward(self, speed, seconds):
        self.dog.move("x", -speed)
        time.sleep(seconds)
        self.dog.move("x", 0)

    def move_left(self, speed, seconds):
        self.dog.move("y", speed)
        time.sleep(seconds)
        self.dog.move("y", 0)

    def move_right(self, speed, seconds):
        self.dog.move("y", -speed)
        time.sleep(seconds)
        self.dog.move("y", 0)

    def turn(self, speed, seconds):
        self.dog.turn(speed)
        time.sleep(seconds)
        self.dog.turn(0)

    def stop(self):
        try:
            self.dog.move("x", 0)
            self.dog.move("y", 0)
            self.dog.turn(0)
        except Exception:
            pass

    def prepare_grab_pose(self):
        """蹲下+低头并保持。"""
        self.dog.translation(["z"], [75])
        self.dog.attitude(["p"], [15])
        time.sleep(1.0)

    def reset_pose(self):
        """恢复站立姿态。"""
        self.dog.attitude(["p"], [0])
        self.dog.translation(["z"], [100])

    def grab_ball(self):
        """来自红球抓取流程文档第6节，实测可稳定抓到红球的动作序列"""
        self.dog.claw(0)
        time.sleep(0.3)
        self.dog.motor([52, 53], [19, 6])
        time.sleep(0.6)
        self.dog.motor([52, 53], [-12, 78])
        time.sleep(0.8)
        self.dog.claw(215)
        time.sleep(0.6)
        self.dog.motor([52, 53], [20, -20])
        time.sleep(0.6)
        self.dog.attitude(["p"], [0])
        time.sleep(0.4)
        self.dog.motor([52, 53], [-13, -20])
        time.sleep(0.6)


# ---------------------- 状态机主流程 ---------------------- #

class GrabResult:
    def __init__(self):
        self.success = False
        self.reason = ""
        self.steps_used = 0
        self.final_detection = None


def confirm_ready(camera, cfg, save_debug):
    ok_count = 0
    max_tries = cfg["confirm_frames"] * 3
    target_color = cfg.get("target_color", "red")
    for i in range(max_tries):
        frame = camera.capture_bgr()
        detection = detect_colored_ball(frame, target_color)

        if save_debug:
            annotated = annotate_frame(frame, detection, "confirm_{}".format(i))
            cv2.imwrite(os.path.join(OUTPUT_DIR, "confirm_{:02d}.jpg".format(i)), annotated)

        if detection is None:
            ok_count = 0
            time.sleep(0.2)
            continue

        offset_x = detection["cx"] - FRAME_CENTER_X
        area = detection["area_ratio"]
        centered = abs(offset_x) <= cfg["center_tolerance"]
        distance_ok = cfg["ready_area_min"] <= area <= cfg["ready_area_max"]

        if centered and distance_ok:
            ok_count += 1
            if ok_count >= cfg["confirm_frames"]:
                return True
        else:
            ok_count = 0

        time.sleep(0.2)

    return False


def run_grab_flow(cfg=None, save_debug=True, stream=True, stream_port=8089):
    cfg = cfg or DEFAULT_CONFIG
    target_color = cfg.get("target_color", "red")
    target_label = color_label(target_color)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    kill_official_services()

    camera = Camera()
    controller = DogController()
    streamer = VideoStreamer(port=stream_port) if stream else None
    if streamer:
        streamer.start()
    result = GrabResult()

    state = "SEARCHING"
    step = 0
    lost_count = 0
    never_found_count = 0
    crouched = False
    ever_found = False
    reached_window = False

    # 【修复#3 核心】用峰值面积(max)而非最后一帧面积(last)
    # 球掉出画面前最后几帧只看到半个球，面积在下降:
    #   实际: 0.03 → 0.04(峰值) → 0.025(半个球) → 0.012 → None
    #   max_seen_area = 0.04  ← 记住了最大值
    #   last_seen_area = 0.012 ← 之前用这个，永远不够
    max_seen_area = 0.0

    try:
        while step < cfg["max_steps"]:
            step += 1
            frame = camera.capture_bgr()
            detection = detect_colored_ball(frame, target_color)
            status_text = "step{:02d}_{}".format(step, state)
            annotated = annotate_frame(frame, detection, status_text)
            if streamer:
                streamer.update(annotated)
            if frame_bus is not None:
                frame_bus.publish_bgr(annotated, "grab")

            if save_debug:
                cv2.imwrite(os.path.join(OUTPUT_DIR, status_text + ".jpg"), annotated)

            # ---------------- 搜寻状态 ---------------- #
            if state == "SEARCHING":
                if detection is not None:
                    print("[{}] 搜寻中发现{}，停止转身".format(step, target_label))
                    controller.stop()
                    time.sleep(0.3)
                    ever_found = True
                    lost_count = 0

                    # 【修复#1】一发现就蹲下
                    if not crouched:
                        print("[{}] 首次发现目标，立即蹲下低头".format(step))
                        controller.prepare_grab_pose()
                        crouched = True
                        time.sleep(0.3)

                    state = "APPROACHING"
                    continue

                never_found_count += 1
                if not ever_found and never_found_count >= cfg["max_search_no_detect"]:
                    result.reason = "搜寻超时，从未发现{}".format(target_label)
                    break

                print("[{}] 搜寻中未发现{}，转身继续找".format(step, target_label))
                controller.turn(cfg["search_turn_speed"], cfg["search_turn_seconds"])
                time.sleep(0.2)
                continue

            # ---------------- 靠近状态 ---------------- #
            if state == "APPROACHING":
                if detection is None:
                    lost_count += 1
                    print("[{}] 丢失{} (连续{}次, 峰值面积={:.4f})".format(
                        step, target_label, lost_count, max_seen_area))

                    # 【修复#3 核心】盲抓判断：用峰值面积
                    if (max_seen_area >= cfg["blind_grab_area"]
                            and lost_count >= cfg["blind_grab_lost_frames"]):
                        print("    -> 峰值面积{:.4f}≥{:.4f}，球在脚下，直接抓取！".format(
                            max_seen_area, cfg["blind_grab_area"]))
                        controller.stop()
                        time.sleep(0.3)
                        controller.grab_ball()
                        reached_window = True
                        result.final_detection = {
                            "area_ratio": max_seen_area, "blind_grab": True
                        }
                        result.reason = "球近距离消失，盲抓(峰值面积{:.4f})".format(max_seen_area)
                        break

                    if lost_count >= cfg["lost_target_limit"]:
                        print("    -> 丢失次数过多且峰值面积不够，退回搜寻")
                        controller.stop()
                        state = "SEARCHING"
                        lost_count = 0
                        max_seen_area = 0.0  # 重新开始追踪
                        if crouched:
                            controller.reset_pose()
                            crouched = False
                            time.sleep(0.5)
                    continue

                lost_count = 0
                offset_x = detection["cx"] - FRAME_CENTER_X
                area = detection["area_ratio"]
                max_seen_area = max(max_seen_area, area)  # 更新峰值
                print("[{}] cx偏移={:+.1f}px area={:.4f} 峰值={:.4f} 蹲姿={}".format(
                    step, offset_x, area, max_seen_area, crouched))

                # 1) 横向对齐
                if abs(offset_x) > cfg["center_tolerance"]:
                    if offset_x > 0:
                        controller.move_right(cfg["align_speed"], cfg["align_seconds"])
                        print("    -> {}偏右，右移对齐".format(target_label))
                    else:
                        controller.move_left(cfg["align_speed"], cfg["align_seconds"])
                        print("    -> {}偏左，左移对齐".format(target_label))
                    continue

                # 2) 居中后判断远近
                if area < cfg["ready_area_min"]:
                    if area >= cfg["fine_approach_area"]:
                        spd = cfg["approach_speed"]
                        sec = cfg["approach_seconds"] * 0.5
                        controller.move_forward(spd, sec)
                        print("    -> 较近，小步前进(速度={}, 时间={:.2f}s)".format(spd, sec))
                    else:
                        controller.move_forward(cfg["approach_speed"], cfg["approach_seconds"])
                        print("    -> 太远，正常前进")
                    continue

                if area > cfg["ready_area_max"]:
                    spd = cfg["approach_speed"]
                    sec = cfg["approach_seconds"] * 0.5
                    controller.move_backward(spd, sec)
                    print("    -> 太近，小步后退")
                    continue

                # 3) 满足条件，进入抓取前确认
                print("    -> 已进入抓取窗口，开始确认")
                controller.stop()
                time.sleep(0.5)

                if confirm_ready(camera, cfg, save_debug):
                    reached_window = True
                    result.final_detection = detection
                    break
                else:
                    print("    -> 确认未通过，继续修正")
                    continue

        result.steps_used = step
        if not reached_window and not result.reason:
            result.reason = "已达到最大步数({})，未能稳定进入抓取窗口".format(cfg["max_steps"])

        if reached_window:
            # 盲抓路径已经在循环里调了 grab_ball()，不要重复调
            is_blind = (isinstance(result.final_detection, dict)
                        and result.final_detection.get("blind_grab"))
            if not is_blind:
                print(">>> 确认通过，执行机械臂抓取动作")
                controller.grab_ball()

            final_frame = camera.capture_bgr()
            cv2.imwrite(os.path.join(OUTPUT_DIR, "grab_result.jpg"), final_frame)
            controller.reset_pose()
            result.success = True
            if not result.reason:
                result.reason = "抓取动作已执行完成"
        else:
            print(">>> 抓取流程未成功: {}".format(result.reason))
            if crouched:
                controller.reset_pose()
    except KeyboardInterrupt:
        print("\n收到退出指令，准备关闭程序。")

    result_json = {
        "success": result.success,
        "reason": result.reason,
        "steps_used": result.steps_used,
        "final_detection": result.final_detection,
        "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
    }
    json_path = os.path.join(OUTPUT_DIR, "grab_result.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result_json, f, ensure_ascii=False, indent=2)
    print("运行结果已保存到: {}".format(json_path))

    if streamer:
        print("实时画面继续保留，按 Ctrl-C 关闭程序。")
        try:
            while True:
                frame = camera.capture_bgr()
                detection = detect_colored_ball(frame, target_color)
                annotated = annotate_frame(frame, detection, "DONE")
                streamer.update(annotated)
                time.sleep(0.05)
        except KeyboardInterrupt:
            print("\n收到退出指令，关闭实时画面。")

    controller.stop()
    camera.close()
    if streamer:
        streamer.stop()

    return result


# ---------------------- 独立调试模式 ---------------------- #

def run_detect_only(frames=20, interval=0.3, target_color="red"):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    kill_official_services()
    camera = Camera()
    target_label = color_label(target_color)
    try:
        for i in range(frames):
            frame = camera.capture_bgr()
            detection = detect_colored_ball(frame, target_color)
            annotated = annotate_frame(frame, detection, "detect_{}".format(i))
            path = os.path.join(OUTPUT_DIR, "detect_{:02d}.jpg".format(i))
            cv2.imwrite(path, annotated)
            if detection:
                print("[{}] 检测到{}: cx={:.1f} area={:.4f} conf={:.2f} -> {}".format(
                    i, target_label, detection["cx"], detection["area_ratio"], detection["confidence"], path))
            else:
                print("[{}] 未检测到{} -> {}".format(i, target_label, path))
            time.sleep(interval)
    finally:
        camera.close()
    print("检测测试完成，请去 {} 下载图片确认识别效果".format(OUTPUT_DIR))


def run_test_move():
    kill_official_services()
    controller = DogController()
    try:
        print("即将测试: 前进 -> 后退 -> 左移 -> 右移 -> 转身，每个动作0.5秒")
        input("确认机器狗周围有足够空间，按回车开始...")

        print("前进测试...")
        controller.move_forward(8, 0.5)
        time.sleep(1)

        print("后退测试...")
        controller.move_backward(8, 0.5)
        time.sleep(1)

        print("左移测试...")
        controller.move_left(6, 0.5)
        time.sleep(1)

        print("右移测试...")
        controller.move_right(6, 0.5)
        time.sleep(1)

        print("转身测试...")
        controller.turn(8, 0.5)
        time.sleep(1)

        print("测试完成。")
    finally:
        controller.stop()


# ---------------------- 命令行入口 ---------------------- #

def main():
    parser = argparse.ArgumentParser(description="颜色球识别-靠近-抓取")
    parser.add_argument("--mode", choices=["full", "detect", "test-move"], default="full",
                         help="full=完整流程; detect=只测识别; test-move=只测移动API")
    parser.add_argument(
        "--target-color",
        choices=sorted(COLOR_HSV_RANGES),
        default=DEFAULT_CONFIG["target_color"],
        help="目标球颜色",
    )
    parser.add_argument("--max-steps", type=int, default=DEFAULT_CONFIG["max_steps"])
    parser.add_argument("--center-tolerance", type=int, default=DEFAULT_CONFIG["center_tolerance"])
    parser.add_argument("--ready-area-min", type=float, default=DEFAULT_CONFIG["ready_area_min"])
    parser.add_argument("--ready-area-max", type=float, default=DEFAULT_CONFIG["ready_area_max"])
    parser.add_argument("--approach-speed", type=int, default=DEFAULT_CONFIG["approach_speed"])
    parser.add_argument("--approach-seconds", type=float, default=DEFAULT_CONFIG["approach_seconds"])
    parser.add_argument("--align-speed", type=int, default=DEFAULT_CONFIG["align_speed"])
    parser.add_argument("--align-seconds", type=float, default=DEFAULT_CONFIG["align_seconds"])
    parser.add_argument("--confirm-frames", type=int, default=DEFAULT_CONFIG["confirm_frames"])
    parser.add_argument("--search-turn-speed", type=int, default=DEFAULT_CONFIG["search_turn_speed"])
    parser.add_argument("--search-turn-seconds", type=float, default=DEFAULT_CONFIG["search_turn_seconds"])
    parser.add_argument("--max-search-no-detect", type=int, default=DEFAULT_CONFIG["max_search_no_detect"])
    parser.add_argument("--blind-grab-area", type=float, default=DEFAULT_CONFIG["blind_grab_area"],
                         help="峰值面积≥此值且球消失时直接抓(默认0.015)")
    parser.add_argument("--blind-grab-lost-frames", type=int, default=DEFAULT_CONFIG["blind_grab_lost_frames"],
                         help="连续丢失几帧才触发盲抓(默认2)")
    parser.add_argument("--fine-approach-area", type=float, default=DEFAULT_CONFIG["fine_approach_area"],
                         help="面积≥此值时前进步幅减半(默认0.025)")
    parser.add_argument("--no-debug", action="store_true", help="不保存每一步的调试图片")
    parser.add_argument("--no-stream", action="store_true", help="关闭电脑浏览器实时画面")
    parser.add_argument("--stream-port", type=int, default=8089, help="电脑实时画面端口")
    parser.add_argument("--detect-frames", type=int, default=20, help="detect模式下采集的帧数")
    args = parser.parse_args()

    if args.mode == "test-move":
        run_test_move()
        return

    if args.mode == "detect":
        run_detect_only(frames=args.detect_frames, target_color=args.target_color)
        return

    cfg = dict(DEFAULT_CONFIG)
    cfg.update(
        target_color=args.target_color,
        max_steps=args.max_steps,
        center_tolerance=args.center_tolerance,
        ready_area_min=args.ready_area_min,
        ready_area_max=args.ready_area_max,
        approach_speed=args.approach_speed,
        approach_seconds=args.approach_seconds,
        align_speed=args.align_speed,
        align_seconds=args.align_seconds,
        confirm_frames=args.confirm_frames,
        search_turn_speed=args.search_turn_speed,
        search_turn_seconds=args.search_turn_seconds,
        max_search_no_detect=args.max_search_no_detect,
        blind_grab_area=args.blind_grab_area,
        blind_grab_lost_frames=args.blind_grab_lost_frames,
        fine_approach_area=args.fine_approach_area,
    )

    result = run_grab_flow(
        cfg=cfg,
        save_debug=not args.no_debug,
        stream=not args.no_stream,
        stream_port=args.stream_port,
    )

    print("=" * 40)
    print("结果: {}".format("成功" if result.success else "失败"))
    print("原因: {}".format(result.reason))
    print("用时步数: {}".format(result.steps_used))
    print("=" * 40)


if __name__ == "__main__":
    main()
