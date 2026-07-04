"""跨进程"最新一帧"总线。

各阶段子进程(人脸/抓球/找线/巡线)把最新画面以 JPEG 写到共享内存文件,
总控的网页仪表盘统一读取展示。写入方失败时静默降级,绝不影响主流程。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


def _base_dir() -> Path:
    shm = Path("/dev/shm")
    root = shm if shm.is_dir() else Path(tempfile.gettempdir())
    return root / "housekeeper"


BASE_DIR = _base_dir()
FRAME_PATH = BASE_DIR / "frame.jpg"
META_PATH = BASE_DIR / "frame_meta.json"


def publish_jpeg(jpeg: bytes, source: str = "") -> None:
    """原子地发布一帧 JPEG(先写临时文件再 rename)。"""
    try:
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = FRAME_PATH.with_name(FRAME_PATH.name + ".tmp")
        tmp.write_bytes(jpeg)
        os.replace(tmp, FRAME_PATH)
        meta_tmp = META_PATH.with_name(META_PATH.name + ".tmp")
        meta_tmp.write_text(
            json.dumps({"source": source, "time": time.time()}),
            encoding="utf-8",
        )
        os.replace(meta_tmp, META_PATH)
    except Exception:
        pass


def publish_bgr(frame_bgr, source: str = "", quality: int = 80) -> None:
    """把 BGR ndarray 编码成 JPEG 后发布;cv2 不可用时静默跳过。"""
    try:
        import cv2

        ok, jpeg = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if ok:
            publish_jpeg(jpeg.tobytes(), source)
    except Exception:
        pass


def read_latest(max_age_seconds: float = 3.0) -> tuple[bytes | None, str]:
    """读取最新一帧。超过 max_age_seconds 未更新视为无画面,返回 (None, "")。"""
    try:
        stat = FRAME_PATH.stat()
        if time.time() - stat.st_mtime > max_age_seconds:
            return None, ""
        source = ""
        try:
            source = json.loads(META_PATH.read_text(encoding="utf-8")).get("source", "")
        except Exception:
            source = ""
        return FRAME_PATH.read_bytes(), source
    except Exception:
        return None, ""
