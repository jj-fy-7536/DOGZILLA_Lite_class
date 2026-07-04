#!/home/pi/RaspberryPi-CM5/xgovenv/bin/python
"""Find the black guide line after grabbing, align to it, then exit."""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None

try:
    from xgolib import XGO
except ImportError:
    XGO = None

try:
    import frame_bus
except ImportError:
    frame_bus = None


FRAME_WIDTH = 320
FRAME_HEIGHT = 240
IMAGE_CENTER_X = FRAME_WIDTH // 2

BLACK_MAX_VALUE = 80
BLACK_MAX_CHROMA = 35

LINE_BANDS = ((190, 239), (155, 205), (115, 165), (80, 130))
MIN_LINE_WIDTH = 6
MAX_LINE_WIDTH = 130
EDGE_REJECT_ZONE = 25

ALIGN_TOLERANCE = 20
ALIGN_STABLE_FRAMES = 4
ALIGN_TURN = 18
SCAN_TURN = 24
FRAME_INTERVAL = 0.12


@dataclass
class LineCandidate:
    found: bool
    x: int = IMAGE_CENTER_X
    y: int = 0
    width: int = 0
    score: float = 0.0


def black_mask_from_rgb(frame_rgb: np.ndarray) -> np.ndarray:
    max_channel = frame_rgb.max(axis=2)
    min_channel = frame_rgb.min(axis=2)
    dark = max_channel <= BLACK_MAX_VALUE
    neutral = (max_channel - min_channel) <= BLACK_MAX_CHROMA
    return dark & neutral


def clean_mask(mask: np.ndarray) -> np.ndarray:
    if cv2 is None:
        return np.asarray(mask) > 0
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    binary = np.where(mask, 255, 0).astype(np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    return binary > 0


def find_best_line_candidate(mask: np.ndarray) -> LineCandidate:
    mask = np.asarray(mask) > 0
    if mask.ndim != 2 or mask.size == 0:
        return LineCandidate(False)

    height, width_total = mask.shape
    best = None
    for y0, y1 in LINE_BANDS:
        y0 = max(0, min(y0, height - 1))
        y1 = max(y0, min(y1, height - 1))
        band = mask[y0 : y1 + 1, :]
        min_votes = max(2, int(band.shape[0] * 0.25))
        columns = band.sum(axis=0) >= min_votes
        for start, end in segments(columns):
            width = end - start + 1
            if width < MIN_LINE_WIDTH or width > MAX_LINE_WIDTH:
                continue
            center = (start + end) // 2
            if center < EDGE_REJECT_ZONE or center > width_total - 1 - EDGE_REJECT_ZONE:
                continue
            y = (y0 + y1) // 2
            center_penalty = abs(center - IMAGE_CENTER_X)
            width_bonus = min(width, 45)
            score = y * 2.0 + width_bonus * 1.5 - center_penalty * 1.2
            candidate = LineCandidate(True, int(center), int(y), int(width), float(score))
            if best is None or candidate.score > best.score:
                best = candidate

    return best if best is not None else LineCandidate(False)


def segments(row_mask: np.ndarray) -> list[tuple[int, int]]:
    indices = np.flatnonzero(row_mask)
    if len(indices) == 0:
        return []
    result = []
    start = int(indices[0])
    previous = int(indices[0])
    for value in indices[1:]:
        value = int(value)
        if value == previous + 1:
            previous = value
            continue
        result.append((start, previous))
        start = previous = value
    result.append((start, previous))
    return result


def turn_for_candidate(candidate: LineCandidate, tolerance: int = ALIGN_TOLERANCE) -> int:
    if not candidate.found:
        return 0
    if candidate.x < IMAGE_CENTER_X - tolerance:
        return ALIGN_TURN
    if candidate.x > IMAGE_CENTER_X + tolerance:
        return -ALIGN_TURN
    return 0


def alignment_ready(
    candidate: LineCandidate,
    stable_count: int,
    tolerance: int = ALIGN_TOLERANCE,
    min_score: float = 120.0,
) -> bool:
    return (
        candidate.found
        and candidate.score >= min_score
        and abs(candidate.x - IMAGE_CENTER_X) <= tolerance
        and stable_count >= ALIGN_STABLE_FRAMES
    )


def configure_line_search_pose(dog) -> None:
    dog.translation(["z"], [100])
    time.sleep(0.2)
    dog.attitude(["p"], [0])
    time.sleep(0.2)
    dog.pace("slow")


def annotate_frame(frame_rgb: np.ndarray, candidate: LineCandidate, status: str) -> np.ndarray:
    if cv2 is None:
        return frame_rgb
    frame = frame_rgb.copy()
    cv2.line(frame, (IMAGE_CENTER_X, 0), (IMAGE_CENTER_X, FRAME_HEIGHT), (255, 255, 0), 1)
    if candidate.found:
        cv2.circle(frame, (candidate.x, candidate.y), 6, (255, 0, 0), 2)
        cv2.line(frame, (candidate.x, 0), (candidate.x, FRAME_HEIGHT), (255, 0, 0), 1)
    cv2.putText(frame, status, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
    return frame


class Camera:
    def __init__(self):
        if Picamera2 is None:
            raise RuntimeError("picamera2 is unavailable")
        self.cam = Picamera2()
        self.cam.configure(
            self.cam.create_preview_configuration(main={"format": "RGB888", "size": (FRAME_WIDTH, FRAME_HEIGHT)})
        )
        self.cam.start()
        time.sleep(0.8)

    def capture_rgb(self) -> np.ndarray:
        return self.cam.capture_array()

    def close(self) -> None:
        self.cam.stop()
        self.cam.close()


class DogController:
    def __init__(self):
        if XGO is None:
            raise RuntimeError("xgolib is unavailable")
        self.dog = XGO(port="/dev/ttyAMA0", version="xgolite")
        configure_line_search_pose(self.dog)

    def stop(self) -> None:
        self.dog.move("x", 0)
        self.dog.move("y", 0)
        self.dog.turn(0)
        self.dog.stop()

    def search(self, turn_speed: int) -> None:
        self.dog.move("x", 0)
        self.dog.turn(turn_speed)

    def align(self, turn_speed: int) -> None:
        self.dog.move("x", 0)
        self.dog.turn(turn_speed)


def detect_candidate(frame_rgb: np.ndarray) -> LineCandidate:
    mask = clean_mask(black_mask_from_rgb(frame_rgb))
    return find_best_line_candidate(mask)


def run_alignment(args) -> bool:
    camera = Camera()
    dog = DogController()
    stable_count = 0
    started = time.monotonic()
    last_candidate = LineCandidate(False)

    def publish(frame_rgb: np.ndarray, candidate: LineCandidate, status: str) -> None:
        if frame_bus is None or cv2 is None:
            return
        annotated = annotate_frame(frame_rgb, candidate, status)
        frame_bus.publish_bgr(cv2.cvtColor(annotated, cv2.COLOR_RGB2BGR), "align")

    try:
        while time.monotonic() - started < args.scan_seconds:
            frame = camera.capture_rgb()
            candidate = detect_candidate(frame)
            if candidate.found and candidate.score >= args.min_score:
                last_candidate = candidate
                turn = turn_for_candidate(candidate, args.tolerance)
                if turn == 0:
                    stable_count += 1
                    if alignment_ready(candidate, stable_count, args.tolerance, args.min_score):
                        dog.stop()
                        publish(frame, candidate, "ALIGN_SUCCESS")
                        print("ALIGN_SUCCESS x={} y={} score={:.1f}".format(candidate.x, candidate.y, candidate.score))
                        return True
                    dog.align(0)
                    publish(frame, candidate, "HOLD")
                    print("HOLD x={} y={} score={:.1f}".format(candidate.x, candidate.y, candidate.score))
                else:
                    stable_count = 0
                    dog.align(turn)
                    publish(frame, candidate, "ALIGN turn={}".format(turn))
                    print("ALIGN x={} y={} turn={} score={:.1f}".format(candidate.x, candidate.y, turn, candidate.score))
            else:
                stable_count = 0
                dog.search(args.scan_turn)
                publish(frame, candidate, "SEARCH")
                if last_candidate.found:
                    print("SEARCH lost_last_x={} score={:.1f}".format(last_candidate.x, last_candidate.score))
                else:
                    print("SEARCH no_candidate")
            time.sleep(args.frame_interval)

        dog.stop()
        print("ALIGN_FAILED")
        return False
    finally:
        try:
            dog.stop()
        finally:
            camera.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Find and align to the black guide line before line following.")
    parser.add_argument("--scan-seconds", type=float, default=10.0)
    parser.add_argument("--scan-turn", type=int, default=-SCAN_TURN)
    parser.add_argument("--tolerance", type=int, default=ALIGN_TOLERANCE)
    parser.add_argument("--stable-frames", type=int, default=ALIGN_STABLE_FRAMES)
    parser.add_argument("--min-score", type=float, default=120.0)
    parser.add_argument("--frame-interval", type=float, default=FRAME_INTERVAL)
    args = parser.parse_args()
    return 0 if run_alignment(args) else 1


if __name__ == "__main__":
    raise SystemExit(main())
