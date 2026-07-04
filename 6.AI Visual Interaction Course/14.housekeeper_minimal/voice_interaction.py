#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DOGZILLA-Lite voice interaction module.

Run on the robot:
    python3 voice_interaction.py

Required Xunfei env vars, unless you fill the constants below:
    XFYUN_APPID
    XFYUN_API_KEY
    XFYUN_API_SECRET
"""

from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import hmac
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import wave
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from time import mktime
from typing import Callable
from urllib.parse import urlencode
from urllib.parse import quote
from urllib.request import Request
from urllib.request import urlopen
from wsgiref.handlers import format_date_time

import websocket


ROBOT_PATHS = ("/home/pi/RaspberryPi-CM5/app", "/home/pi/RaspberryPi-CM5/demos")
for robot_path in ROBOT_PATHS:
    if robot_path not in sys.path:
        sys.path.append(robot_path)

MUSIC_DIR = Path("/home/pi/xgoMusic")
DEFAULT_RECORD_NAME = "voice_cmd"
DEFAULT_MUSIC_PLAYER = Path(os.getenv("DOGZILLA_MUSIC_PLAYER", "/home/pi/dogzilla_runs/dogzilla_music_player.py"))
DEFAULT_DOGZILLA_MUSIC_DIR = Path(os.getenv("DOGZILLA_MUSIC_DIR", "/home/pi/dogzilla_runs/music"))
DEFAULT_WEATHER_CITY = os.getenv("DOGZILLA_WEATHER_CITY", "")
DEFAULT_WEATHER_CITY_LABEL = os.getenv("DOGZILLA_WEATHER_CITY_LABEL", "")
DEFAULT_WEATHER_LAT = os.getenv("DOGZILLA_WEATHER_LAT")
DEFAULT_WEATHER_LON = os.getenv("DOGZILLA_WEATHER_LON")

# You can also put keys here, but using env vars is cleaner.
XFYUN_APPID = os.getenv("XFYUN_APPID", "")
XFYUN_API_KEY = os.getenv("XFYUN_API_KEY", "")
XFYUN_API_SECRET = os.getenv("XFYUN_API_SECRET", "")
XFYUN_TTS_APPID = os.getenv("XFYUN_TTS_APPID", XFYUN_APPID)
XFYUN_TTS_API_KEY = os.getenv("XFYUN_TTS_API_KEY", XFYUN_API_KEY)
XFYUN_TTS_API_SECRET = os.getenv("XFYUN_TTS_API_SECRET", XFYUN_API_SECRET)
SPARK_API_PASSWORD = os.getenv("SPARK_API_PASSWORD", "")
SPARK_API_URL = os.getenv("SPARK_API_URL", "https://spark-api-open.xf-yun.com/v1/chat/completions")
SPARK_MODEL = os.getenv("SPARK_MODEL", "lite")
XFYUN_TTS_VCN = os.getenv("XFYUN_TTS_VCN", "x4_xiaoyan")
XFYUN_TTS_SPEED = int(os.getenv("XFYUN_TTS_SPEED", "50"))
XFYUN_TTS_VOLUME = int(os.getenv("XFYUN_TTS_VOLUME", "70"))
XFYUN_TTS_PITCH = int(os.getenv("XFYUN_TTS_PITCH", "50"))
XFYUN_TTS_TIMEOUT = float(os.getenv("XFYUN_TTS_TIMEOUT", "10.0"))
SPARK_SYSTEM_PROMPT = os.getenv(
    "SPARK_SYSTEM_PROMPT",
    "你是DOGZILLA Lite机器狗的语音助手。请用中文口语化回答，简短自然，适合直接朗读；"
    "一般不超过80个汉字。不要承诺播放音乐、闹钟、拍照、导航等未接入功能；"
    "除普通问答、背诗、讲故事、介绍知识外，目前可执行指令只有：坐下、握手、站起来、停止、前进、后退、左转、右转；"
    "问天气时需要用户说出城市。",
)

STATUS_FIRST_FRAME = 0
STATUS_CONTINUE_FRAME = 1
STATUS_LAST_FRAME = 2


@dataclass
class Command:
    name: str
    reply: str = ""
    text: str = ""


class XunfeiTTS:
    def __init__(
        self,
        appid: str,
        api_key: str,
        api_secret: str,
        *,
        vcn: str = XFYUN_TTS_VCN,
        speed: int = XFYUN_TTS_SPEED,
        volume: int = XFYUN_TTS_VOLUME,
        pitch: int = XFYUN_TTS_PITCH,
        timeout: float = XFYUN_TTS_TIMEOUT,
        output_file: Path | None = None,
    ) -> None:
        self.appid = appid
        self.api_key = api_key
        self.api_secret = api_secret
        self.vcn = vcn
        self.speed = int(speed)
        self.volume = int(volume)
        self.pitch = int(pitch)
        self.timeout = float(timeout)
        self.output_file = output_file or (MUSIC_DIR / "xunfei_tts.wav")

    def ready(self) -> bool:
        return bool(self.appid and self.api_key and self.api_secret)

    def create_url(self) -> str:
        host = "tts-api.xfyun.cn"
        path = "/v2/tts"
        url = "wss://{}{}".format(host, path)
        date = format_date_time(time.time())

        signature_origin = "host: {}\n".format(host)
        signature_origin += "date: {}\n".format(date)
        signature_origin += "GET {} HTTP/1.1".format(path)
        signature_sha = hmac.new(
            self.api_secret.encode("utf-8"),
            signature_origin.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        signature = base64.b64encode(signature_sha).decode("utf-8")

        authorization_origin = (
            'api_key="{}", algorithm="hmac-sha256", '
            'headers="host date request-line", signature="{}"'
        ).format(self.api_key, signature)
        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")
        return url + "?" + urlencode({"authorization": authorization, "date": date, "host": host})

    def build_request_payload(self, text: str) -> dict[str, object]:
        return {
            "common": {"app_id": self.appid},
            "business": {
                "aue": "raw",
                "auf": "audio/L16;rate=16000",
                "vcn": self.vcn,
                "tte": "UTF8",
                "speed": self.speed,
                "volume": self.volume,
                "pitch": self.pitch,
            },
            "data": {
                "status": 2,
                "text": base64.b64encode(text.encode("utf-8")).decode("utf-8"),
            },
        }

    def synthesize_pcm(self, text: str) -> bytes:
        if not self.ready():
            raise RuntimeError("Missing XFYUN_APPID / XFYUN_API_KEY / XFYUN_API_SECRET")
        ws = websocket.create_connection(
            self.create_url(),
            timeout=self.timeout,
            sslopt={"cert_reqs": ssl.CERT_NONE},
        )
        try:
            ws.send(json.dumps(self.build_request_payload(text), ensure_ascii=False))
            audio_parts: list[bytes] = []
            deadline = time.time() + self.timeout
            while time.time() < deadline:
                message = ws.recv()
                payload = json.loads(message)
                if payload.get("code") != 0:
                    raise RuntimeError(payload.get("message", payload))
                data = payload.get("data") or {}
                audio = data.get("audio")
                if audio:
                    audio_parts.append(base64.b64decode(audio))
                if data.get("status") == 2:
                    break
            if not audio_parts:
                raise RuntimeError("Xunfei TTS returned no audio")
            return b"".join(audio_parts)
        finally:
            try:
                ws.close()
            except Exception:
                pass

    def save_pcm_as_wav(self, pcm: bytes, output_file: Path) -> Path:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_file), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(pcm)
        return output_file

    def synthesize_to_wav(self, text: str, output_file: Path | None = None) -> Path:
        output = output_file or self.output_file
        return self.save_pcm_as_wav(self.synthesize_pcm(text), output)

    def play_wav(self, wav_file: Path) -> None:
        players = (
            ("aplay", ["aplay", "-q", str(wav_file)]),
            ("mplayer", ["mplayer", "-really-quiet", str(wav_file)]),
            ("afplay", ["afplay", str(wav_file)]),
        )
        for executable, command in players:
            if shutil.which(executable):
                subprocess.run(command, check=True)
                return
        raise RuntimeError("No WAV player found; expected aplay or mplayer")

    def speak(self, text: str) -> None:
        self.play_wav(self.synthesize_to_wav(text))


class XunfeiIAT:
    def __init__(self, appid: str, api_key: str, api_secret: str) -> None:
        self.appid = appid
        self.api_key = api_key
        self.api_secret = api_secret
        self.result = ""
        self.done = threading.Event()

    def ready(self) -> bool:
        return bool(self.appid and self.api_key and self.api_secret)

    def create_url(self) -> str:
        host = "ws-api.xfyun.cn"
        path = "/v2/iat"
        url = f"wss://{host}{path}"
        now = datetime.datetime.now()
        date = format_date_time(mktime(now.timetuple()))

        signature_origin = f"host: {host}\n"
        signature_origin += f"date: {date}\n"
        signature_origin += f"GET {path} HTTP/1.1"
        signature_sha = hmac.new(
            self.api_secret.encode("utf-8"),
            signature_origin.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        signature = base64.b64encode(signature_sha).decode("utf-8")

        authorization_origin = (
            f'api_key="{self.api_key}", algorithm="hmac-sha256", '
            f'headers="host date request-line", signature="{signature}"'
        )
        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")
        return url + "?" + urlencode({"authorization": authorization, "date": date, "host": host})

    def recognize(self, audio_file: Path, timeout: float = 12.0) -> str:
        if not self.ready():
            raise RuntimeError("Missing XFYUN_APPID / XFYUN_API_KEY / XFYUN_API_SECRET")
        if not audio_file.exists():
            raise FileNotFoundError(str(audio_file))

        self.result = ""
        self.done.clear()
        ws = websocket.WebSocketApp(
            self.create_url(),
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        ws.on_open = lambda socket: self._on_open(socket, audio_file)

        thread = threading.Thread(
            target=lambda: ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE}),
            daemon=True,
        )
        thread.start()
        self.done.wait(timeout=timeout)
        try:
            ws.close()
        except Exception:
            pass
        return clean_text(self.result)

    def _on_message(self, _ws: websocket.WebSocketApp, message: str) -> None:
        try:
            payload = json.loads(message)
            if payload.get("code") != 0:
                print("[ASR] error:", payload.get("message", payload), flush=True)
                return
            data = payload.get("data", {}).get("result", {}).get("ws", [])
            words = []
            for item in data:
                for candidate in item.get("cw", []):
                    words.append(candidate.get("w", ""))
            self.result += "".join(words)
        except Exception as exc:
            print("[ASR] parse error:", repr(exc), flush=True)

    def _on_error(self, _ws: websocket.WebSocketApp, error: object) -> None:
        print("[ASR] websocket error:", error, flush=True)
        self.done.set()

    def _on_close(self, _ws: websocket.WebSocketApp, _code: object, _msg: object) -> None:
        self.done.set()

    def _on_open(self, ws: websocket.WebSocketApp, audio_file: Path) -> None:
        def send_audio() -> None:
            frame_size = 8000
            interval = 0.04
            status = STATUS_FIRST_FRAME

            with audio_file.open("rb") as fp:
                while True:
                    buf = fp.read(frame_size)
                    if not buf:
                        status = STATUS_LAST_FRAME

                    if status == STATUS_FIRST_FRAME:
                        payload = {
                            "common": {"app_id": self.appid},
                            "business": {
                                "domain": "iat",
                                "language": "zh_cn",
                                "accent": "mandarin",
                                "vinfo": 1,
                                "vad_eos": 10000,
                            },
                            "data": {
                                "status": 0,
                                "format": "audio/L16;rate=16000",
                                "audio": base64.b64encode(buf).decode("utf-8"),
                                "encoding": "raw",
                            },
                        }
                        ws.send(json.dumps(payload))
                        status = STATUS_CONTINUE_FRAME
                    elif status == STATUS_CONTINUE_FRAME:
                        payload = {
                            "data": {
                                "status": 1,
                                "format": "audio/L16;rate=16000",
                                "audio": base64.b64encode(buf).decode("utf-8"),
                                "encoding": "raw",
                            }
                        }
                        ws.send(json.dumps(payload))
                    else:
                        payload = {
                            "data": {
                                "status": 2,
                                "format": "audio/L16;rate=16000",
                                "audio": base64.b64encode(buf).decode("utf-8"),
                                "encoding": "raw",
                            }
                        }
                        ws.send(json.dumps(payload))
                        time.sleep(1)
                        break
                    time.sleep(interval)
            ws.close()

        threading.Thread(target=send_audio, daemon=True).start()

    def stream_until_command(
        self,
        command_parser: Callable[[str], Command | None],
        *,
        rate: int = 16000,
        fmt: str = "S16_LE",
        device: str = "",
        frame_ms: int = 40,
        window_seconds: float = 10.0,
        recv_timeout: float = 0.02,
        vad_eos: int = 800,
        use_sudo: bool = True,
        debug: bool = False,
    ) -> tuple[str, Command | None]:
        """Stream microphone audio to IAT and return as soon as a command is heard."""
        if not self.ready():
            raise RuntimeError("Missing XFYUN_APPID / XFYUN_API_KEY / XFYUN_API_SECRET")

        sample_width = {"S16_LE": 2, "S32_LE": 4}.get(fmt.upper(), 2)
        frame_size = max(320, int(rate * sample_width * frame_ms / 1000))
        transcript = ""
        proc: subprocess.Popen[bytes] | None = None
        ws = None

        try:
            proc = self._open_arecord(rate=rate, fmt=fmt, device=device, use_sudo=use_sudo)
            ws = websocket.create_connection(
                self.create_url(),
                timeout=5,
                sslopt={"cert_reqs": ssl.CERT_NONE},
            )
            ws.settimeout(recv_timeout)

            status = STATUS_FIRST_FRAME
            deadline = time.time() + window_seconds
            while time.time() < deadline:
                assert proc.stdout is not None
                buf = proc.stdout.read(frame_size)
                if not buf:
                    break

                ws.send(self._audio_payload(status, buf, rate=rate, vad_eos=vad_eos))
                if status == STATUS_FIRST_FRAME:
                    status = STATUS_CONTINUE_FRAME

                new_text, speech_done = self._drain_stream_messages(ws, recv_timeout=recv_timeout)
                if new_text:
                    transcript += new_text
                    cleaned = clean_text(transcript)
                    if debug:
                        print("[ASR_STREAM]", cleaned, flush=True)
                    command = command_parser(cleaned)
                    if command is not None:
                        return cleaned, command
                    if speech_done:
                        return cleaned, None

            try:
                if ws is not None:
                    ws.send(self._audio_payload(STATUS_LAST_FRAME, b"", rate=rate, vad_eos=vad_eos))
                    final_text, _ = self._drain_stream_messages(ws, recv_timeout=0.1)
                    transcript += final_text
            except Exception:
                pass

            cleaned = clean_text(transcript)
            return cleaned, command_parser(cleaned)
        finally:
            self._stop_process(proc)
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass

    def _audio_payload(self, status: int, buf: bytes, *, rate: int, vad_eos: int) -> str:
        data = {
            "status": status,
            "format": f"audio/L16;rate={rate}",
            "audio": base64.b64encode(buf).decode("utf-8"),
            "encoding": "raw",
        }
        if status == STATUS_FIRST_FRAME:
            payload = {
                "common": {"app_id": self.appid},
                "business": {
                    "domain": "iat",
                    "language": "zh_cn",
                    "accent": "mandarin",
                    "vinfo": 1,
                    "vad_eos": vad_eos,
                },
                "data": data,
            }
        else:
            payload = {"data": data}
        return json.dumps(payload)

    def _drain_stream_messages(self, ws: websocket.WebSocket, *, recv_timeout: float) -> tuple[str, bool]:
        parts: list[str] = []
        speech_done = False
        end_at = time.time() + max(0.0, recv_timeout)
        while time.time() <= end_at:
            try:
                message = ws.recv()
            except websocket.WebSocketTimeoutException:
                break
            except Exception:
                break
            text, is_final = self._extract_text(message)
            if text:
                parts.append(text)
            speech_done = speech_done or is_final
        return "".join(parts), speech_done

    def _extract_text(self, message: str) -> tuple[str, bool]:
        try:
            payload = json.loads(message)
            if payload.get("code") != 0:
                print("[ASR] error:", payload.get("message", payload), flush=True)
                return "", True
            data = payload.get("data", {})
            result = data.get("result", {})
            ws_items = result.get("ws", [])
            words = []
            for item in ws_items:
                for candidate in item.get("cw", []):
                    words.append(candidate.get("w", ""))
            is_final = data.get("status") == 2 or bool(result.get("ls"))
            return "".join(words), is_final
        except Exception as exc:
            print("[ASR] parse error:", repr(exc), flush=True)
            return "", False

    def _open_arecord(
        self,
        *,
        rate: int,
        fmt: str,
        device: str,
        use_sudo: bool,
    ) -> subprocess.Popen[bytes]:
        base_cmd = ["arecord", "-q", "-f", fmt, "-r", str(rate), "-c", "1", "-t", "raw"]
        if device:
            base_cmd[1:1] = ["-D", device]
        candidates = []
        if use_sudo:
            candidates.append(["sudo", "-n", *base_cmd])
        candidates.append(base_cmd)

        last_error = ""
        for cmd in candidates:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            time.sleep(0.15)
            if proc.poll() is None:
                return proc
            stderr = b""
            try:
                if proc.stderr is not None:
                    stderr = proc.stderr.read()
            except Exception:
                pass
            last_error = stderr.decode("utf-8", "replace").strip()
            print("[AUDIO] arecord failed:", " ".join(cmd), last_error, flush=True)
        raise RuntimeError("Cannot start arecord: " + last_error)

    @staticmethod
    def _stop_process(proc: subprocess.Popen[bytes] | None) -> None:
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=0.4)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


class DogzillaVoiceRobot:
    def __init__(
        self,
        dry_run: bool = False,
        *,
        tts_backend: str = "xunfei",
        tts_appid: str = XFYUN_TTS_APPID,
        tts_api_key: str = XFYUN_TTS_API_KEY,
        tts_api_secret: str = XFYUN_TTS_API_SECRET,
        tts_voice: str = XFYUN_TTS_VCN,
        tts_speed: int = XFYUN_TTS_SPEED,
        tts_volume: int = XFYUN_TTS_VOLUME,
        tts_pitch: int = XFYUN_TTS_PITCH,
        tts_timeout: float = XFYUN_TTS_TIMEOUT,
    ) -> None:
        self.dry_run = dry_run
        self.tts_backend = tts_backend
        self.dog = None
        self.edu = None
        self.tts = None
        self.last_spoken_text = ""
        self.last_spoken_at = 0.0
        if dry_run:
            return

        from xgolib import XGO
        from xgoedu import XGOEDU

        try:
            self.dog = XGO(port="/dev/ttyAMA0", version="xgolite")
        except TypeError:
            self.dog = XGO("xgolite")
        self.edu = XGOEDU()
        if tts_backend == "xunfei":
            self.tts = XunfeiTTS(
                tts_appid,
                tts_api_key,
                tts_api_secret,
                vcn=tts_voice,
                speed=tts_speed,
                volume=tts_volume,
                pitch=tts_pitch,
                timeout=tts_timeout,
            )

    def setup(self) -> None:
        if self.dry_run:
            print("[DRY] setup", flush=True)
            return
        self.safe_call(self.dog, "pace", "slow")
        self.safe_call(self.dog, "gait_type", "trot")
        self.safe_call(self.dog, "reset")
        time.sleep(0.5)

    def stop(self) -> None:
        if self.dry_run:
            print("[DRY] stop", flush=True)
            return
        for _ in range(4):
            self.safe_call(self.dog, "move", "x", 0)
            self.safe_call(self.dog, "move", "y", 0)
            self.safe_call(self.dog, "turn", 0)
            self.safe_call(self.dog, "stop")
            time.sleep(0.04)

    def action(self, action_id: int, seconds: float = 3.0) -> None:
        self.stop()
        if self.dry_run:
            print(f"[DRY] action({action_id})", flush=True)
            return
        self.safe_call(self.dog, "action", action_id)
        time.sleep(seconds)

    def move_x(self, speed: int, seconds: float) -> None:
        if self.dry_run:
            print(f"[DRY] move x={speed} for {seconds}s", flush=True)
            return
        self.safe_call(self.dog, "move", "x", int(speed))
        time.sleep(seconds)
        self.stop()

    def turn(self, speed: int, seconds: float) -> None:
        if self.dry_run:
            print(f"[DRY] turn={speed} for {seconds}s", flush=True)
            return
        self.safe_call(self.dog, "turn", int(speed))
        time.sleep(seconds)
        self.stop()

    def record(self, name: str, seconds: int) -> Path:
        audio_file = MUSIC_DIR / f"{name}.wav"
        if self.dry_run:
            print(f"[DRY] record {seconds}s -> {audio_file}", flush=True)
            return audio_file
        MUSIC_DIR.mkdir(parents=True, exist_ok=True)
        self.safe_call(self.edu, "xgoAudioRecord", filename=name, seconds=seconds)
        return audio_file

    def speak(self, text: str) -> None:
        print("[DOG]", text, flush=True)
        self.last_spoken_text = clean_text(text)
        self.last_spoken_at = time.time()
        if self.dry_run:
            return
        try:
            if self.tts_backend == "xunfei" and self.tts is not None:
                self.tts.speak(text)
                return
        except Exception as exc:
            print("[TTS] xunfei failed, fallback to SpeechSynthesis:", repr(exc), flush=True)
        try:
            # SpeechSynthesis already saves result.wav and plays it through xgoSpeaker.
            self.edu.SpeechSynthesis(text)
        except Exception as exc:
            print("[TTS] skip:", repr(exc), flush=True)
        finally:
            self.last_spoken_at = time.time()

    @staticmethod
    def safe_call(obj: object, method_name: str, *args: object, **kwargs: object) -> None:
        method = getattr(obj, method_name, None)
        if not callable(method):
            return
        try:
            method(*args, **kwargs)
        except Exception as exc:
            print(f"[WARN] {method_name} failed: {exc!r}", flush=True)


def clean_text(text: str) -> str:
    text = text.strip().lower()
    return re.sub(r"[\s，。！？、,.!?;；:：\"'“”‘’]", "", text)


def has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


MUSIC_PLAY_KEYWORDS = (
    "放歌",
    "播放音乐",
    "播放歌曲",
    "放音乐",
    "听歌",
    "来首歌",
    "来一首歌",
    "唱歌",
)
MUSIC_STOP_KEYWORDS = (
    "停歌",
    "停止播放",
    "停止音乐",
    "关音乐",
    "关闭音乐",
    "别放了",
)
SUPPORTED_MUSIC_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


def parse_music_command(text: str) -> str | None:
    cleaned = clean_text(text)
    if not cleaned:
        return None
    if has_any(cleaned, MUSIC_STOP_KEYWORDS):
        return "stop"
    if has_any(cleaned, MUSIC_PLAY_KEYWORDS):
        return "play"
    return None


def build_music_player_command(
    python_executable: Path,
    player_script: Path,
    *,
    music_dir: Path = DEFAULT_DOGZILLA_MUSIC_DIR,
    song: str = "",
    volume: int = 85,
    loop: bool = False,
    action: str = "play",
) -> list[str]:
    command = [
        str(python_executable),
        str(player_script),
        "--music-dir",
        str(music_dir),
        "--volume",
        str(max(0, min(100, int(volume)))),
    ]
    if action == "stop":
        command.append("--stop")
        return command
    command.append("--background")
    resolved_song = song or first_music_song_name(music_dir)
    if resolved_song:
        command.extend(["--song", resolved_song])
    if loop:
        command.append("--loop")
    return command


def first_music_song_name(music_dir: Path) -> str:
    try:
        songs = sorted(
            [
                path
                for path in music_dir.iterdir()
                if path.is_file() and path.suffix.lower() in SUPPORTED_MUSIC_EXTENSIONS
            ],
            key=lambda path: path.name.lower(),
        )
    except OSError:
        return ""
    return songs[0].name if songs else ""


def run_music_command(action: str, args: argparse.Namespace) -> int:
    command = build_music_player_command(
        Path(sys.executable),
        Path(args.music_player),
        music_dir=Path(args.music_dir),
        song=args.music_song,
        volume=args.music_volume,
        loop=args.music_loop,
        action=action,
    )
    print("[MUSIC] command:", " ".join(command), flush=True)
    try:
        result = subprocess.run(command, timeout=args.music_timeout, check=False)
    except Exception as exc:
        print("[MUSIC] failed:", repr(exc), flush=True)
        return 1
    return int(result.returncode)


CHAT_IGNORED_TEXTS = {
    "嗯",
    "啊",
    "哦",
    "好",
    "好的",
    "行",
    "可以",
    "收到",
    "谢谢",
    "不用",
    "没事",
    "算了",
    "喂",
    "你好",
    "哈喽",
    "hello",
    "小狗",
    "机器狗",
    "dogzilla",
}

QUESTION_KEYWORDS = (
    "什么",
    "怎么",
    "怎样",
    "咋",
    "为什么",
    "为何",
    "谁",
    "哪里",
    "哪儿",
    "哪个",
    "哪",
    "几",
    "多少",
    "多大",
    "多远",
    "多久",
    "是不是",
    "有没有",
    "能不能",
    "能否",
    "可不可以",
    "行不行",
    "会不会",
    "要不要",
    "是否",
    "如何",
    "应该",
)

QUESTION_ENDINGS = ("吗", "呢", "么", "嘛")

REQUEST_KEYWORDS = (
    "背一下",
    "背一首",
    "背诵",
    "念一下",
    "读一下",
    "朗诵",
    "讲一下",
    "讲一个",
    "讲个",
    "说一下",
    "说说",
    "介绍一下",
    "介绍介绍",
    "解释一下",
    "翻译一下",
    "总结一下",
    "算一下",
    "帮我",
    "给我",
    "告诉我",
    "回答我",
    "查一下",
    "搜一下",
    "想听",
)

WAKE_WORDS = (
    "小狗",
    "狗狗",
    "机器狗",
    "dogzilla",
    "小智",
    "你",
)


def should_use_spark_chat(text: str, args: argparse.Namespace) -> bool:
    cleaned = clean_text(text)
    if not args.spark_chat or not args.spark_api_password:
        return False
    if len(cleaned) < args.spark_min_chars:
        return False
    if cleaned in CHAT_IGNORED_TEXTS:
        return False
    if args.spark_question_only and not (is_question_text(text) or is_directed_request_text(text)):
        return False
    return True


def is_question_text(text: str) -> bool:
    raw = text.strip()
    cleaned = clean_text(text)
    if not cleaned:
        return False
    if "?" in raw or "？" in raw:
        return True
    if has_any(cleaned, QUESTION_KEYWORDS):
        return True
    return cleaned.endswith(QUESTION_ENDINGS)


def is_directed_request_text(text: str) -> bool:
    cleaned = clean_text(text)
    if not cleaned:
        return False
    if has_any(cleaned, REQUEST_KEYWORDS):
        return True
    return has_any(cleaned, WAKE_WORDS) and len(cleaned) >= 4


def is_recent_tts_echo(text: str, robot: DogzillaVoiceRobot, args: argparse.Namespace) -> bool:
    cleaned = clean_text(text)
    spoken = robot.last_spoken_text
    if not cleaned or not spoken:
        return False
    if time.time() - robot.last_spoken_at > args.tts_echo_ignore_seconds:
        return False
    if cleaned == spoken or cleaned in spoken or spoken in cleaned:
        return True
    return SequenceMatcher(None, cleaned, spoken).ratio() >= args.tts_echo_similarity


def is_recent_duplicate_text(text: str, last_text: str, last_at: float, args: argparse.Namespace) -> bool:
    cleaned = clean_text(text)
    if not cleaned or not last_text:
        return False
    if time.time() - last_at > args.same_text_cooldown:
        return False
    if cleaned == last_text or cleaned in last_text or last_text in cleaned:
        return True
    return SequenceMatcher(None, cleaned, last_text).ratio() >= args.same_text_similarity


def fetch_spark_answer(question: str, args: argparse.Namespace) -> str:
    if not args.spark_api_password:
        raise RuntimeError("Missing SPARK_API_PASSWORD")

    now_text = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    speech_prompt = (
        f"{args.spark_system_prompt}\n"
        f"当前时间：{now_text}。\n"
        f"用户问题：{question}\n"
        "请只输出适合机器狗直接朗读的一段回答，不要解释这些规则。"
    )
    payload = {
        "model": args.spark_model,
        "messages": [{"role": "user", "content": speech_prompt}],
        "stream": False,
        "temperature": args.spark_temperature,
        "max_tokens": args.spark_max_tokens,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        args.spark_api_url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {args.spark_api_password}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    with urlopen(request, timeout=args.spark_timeout) as response:
        response_payload = json.loads(response.read().decode("utf-8"))

    if response_payload.get("code") not in (None, 0):
        raise RuntimeError(response_payload.get("message", response_payload))

    choices = response_payload.get("choices") or []
    if not choices:
        raise RuntimeError("Spark returned no choices")

    message = choices[0].get("message") or {}
    answer = message.get("content", "")
    if not answer:
        answer = choices[0].get("text", "")
    return compact_tts_text(answer, max_chars=args.spark_max_reply_chars)


def compact_tts_text(text: str, max_chars: int) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"[*_#>`~-]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text

    clipped = text[:max_chars].rstrip("，。！？,.!?;； ")
    return clipped + "。"


WEATHER_KEYWORDS = ("天气", "气温", "温度", "冷不冷", "热不热", "下雨", "会不会下雨")

WEATHER_FILLER_WORDS = (
    "今天",
    "明天",
    "现在",
    "当前",
    "当地",
    "这边",
    "那边",
    "的",
    "帮我",
    "给我",
    "查一下",
    "查查",
    "查询",
    "告诉我",
    "问一下",
    "小狗",
    "狗狗",
    "机器狗",
    "dogzilla",
    "请",
)


def is_weather_request(text: str) -> bool:
    return has_any(clean_text(text), WEATHER_KEYWORDS)


def extract_weather_city(text: str) -> str:
    cleaned = clean_text(text)
    if not cleaned:
        return ""

    first_key_index = len(cleaned)
    for keyword in WEATHER_KEYWORDS:
        index = cleaned.find(keyword)
        if 0 <= index < first_key_index:
            first_key_index = index

    before_keyword = cleaned[:first_key_index] if first_key_index < len(cleaned) else cleaned
    after_keyword = cleaned[first_key_index:] if first_key_index < len(cleaned) else ""

    candidates = [before_keyword]
    match = re.search(r"(?:查|问|看|告诉我)([\u4e00-\u9fff]{2,8})(?:天气|气温|温度|冷不冷|热不热|下雨)", cleaned)
    if match:
        candidates.insert(0, match.group(1))

    # Handle phrases such as "天气查绍兴" or "天气绍兴怎么样".
    suffix = after_keyword
    for keyword in WEATHER_KEYWORDS:
        suffix = suffix.replace(keyword, "")
    candidates.append(suffix)

    for candidate in candidates:
        city = normalize_weather_city(candidate)
        if city:
            return city
    return ""


def normalize_weather_city(text: str) -> str:
    city = text
    for word in WEATHER_FILLER_WORDS:
        city = city.replace(word, "")
    city = re.sub(r"(怎么样|如何|多少|几度|有没有雨|会不会|吗|呢|呀|啊|吧)$", "", city)
    city = city.strip()
    if city.endswith(("市", "区", "县", "州")) and len(city) > 2:
        return city
    if 2 <= len(city) <= 8 and re.fullmatch(r"[\u4e00-\u9fff]+", city):
        return city
    return ""


WEATHER_CODE_TEXT = {
    0: "晴",
    1: "大部晴朗",
    2: "局部多云",
    3: "阴",
    45: "有雾",
    48: "有雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    56: "冻毛毛雨",
    57: "较强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "较强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷雨",
    96: "雷雨伴小冰雹",
    99: "雷雨伴大冰雹",
}


def fetch_weather_report(args: argparse.Namespace, city: str = "") -> str:
    lat, lon, label = resolve_weather_location(args, city=city)
    query = urlencode(
        {
            "latitude": f"{lat:.6f}",
            "longitude": f"{lon:.6f}",
            "current": ",".join(
                (
                    "temperature_2m",
                    "relative_humidity_2m",
                    "apparent_temperature",
                    "precipitation",
                    "weather_code",
                    "wind_speed_10m",
                )
            ),
            "timezone": "auto",
            "forecast_days": "1",
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{query}"
    with urlopen(url, timeout=args.weather_timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    current = payload.get("current", {})
    temp = current.get("temperature_2m")
    feel = current.get("apparent_temperature")
    humidity = current.get("relative_humidity_2m")
    precipitation = current.get("precipitation")
    wind = current.get("wind_speed_10m")
    code = int(current.get("weather_code", -1))
    weather = WEATHER_CODE_TEXT.get(code, "天气状况未知")

    rain_text = ""
    if isinstance(precipitation, (int, float)) and precipitation > 0:
        rain_text = f"，当前降水量{precipitation:.1f}毫米"

    return (
        f"{label}现在{weather}，气温{float(temp):.0f}度，"
        f"体感{float(feel):.0f}度，湿度{float(humidity):.0f}%，"
        f"风速{float(wind):.0f}公里每小时{rain_text}。"
    )


def resolve_weather_location(args: argparse.Namespace, city: str = "") -> tuple[float, float, str]:
    if city:
        return geocode_weather_city(city, timeout=args.weather_timeout)

    if args.weather_lat is not None and args.weather_lon is not None:
        label = args.weather_city_label or args.weather_city or "当前位置"
        return float(args.weather_lat), float(args.weather_lon), label

    city = args.weather_city
    if not city:
        raise RuntimeError("缺少天气城市")
    return geocode_weather_city(city, timeout=args.weather_timeout, label=args.weather_city_label or city)


def geocode_weather_city(city: str, timeout: float, label: str = "") -> tuple[float, float, str]:
    city = city.strip()
    url = (
        "https://geocoding-api.open-meteo.com/v1/search"
        f"?name={quote(city)}&count=1&language=zh&format=json"
    )
    with urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    results = payload.get("results") or []
    if not results:
        raise RuntimeError(f"找不到天气城市: {city}")
    first = results[0]
    label = label or first.get("name") or city
    return float(first["latitude"]), float(first["longitude"]), label


def parse_command(text: str) -> Command | None:
    text = clean_text(text)
    if not text:
        return None

    music = parse_music_command(text)
    if music == "play":
        return Command("music_play")
    if music == "stop":
        return Command("music_stop")

    # Stop has highest priority.
    if has_any(text, ("停止", "停下", "停", "别动", "不动", "急停")):
        return Command("stop", "已停止")

    # Preset actions from official DOGZILLA examples:
    # Sit_Down -> action(12), Handshake -> action(19).
    if has_any(text, ("坐下", "坐好", "坐", "坐一坐")):
        return Command("sit", "收到，坐下")
    if has_any(text, ("握手", "握个手", "伸手", "来握手")):
        return Command("handshake", "收到，握手")
    if has_any(text, ("站起来", "起立", "站立", "恢复", "复位", "起来")):
        return Command("stand", "收到，站起来")
    if is_weather_request(text):
        city = extract_weather_city(text)
        if city:
            return Command("weather", text=city)
        return Command("say", text="你想问哪个城市的天气？")

    if has_any(text, ("你是谁", "你叫什么", "介绍自己", "介绍一下自己")):
        return Command("say", text="我是DOGZILLA Lite机器狗，可以坐下、握手、走动，也可以回答简单问题。")
    if has_any(text, ("你会什么", "能做什么", "有什么功能")):
        return Command("say", text="我现在会坐下、握手、站起来、停止、前进、后退、左右转、查指定城市天气，也能背诗和回答简单问题。")
    if has_any(text, ("几点", "现在时间", "当前时间", "报时")):
        return Command("time", "")

    # Optional basic motion commands.
    if has_any(text, ("前进", "往前", "向前", "直走")):
        return Command("forward", "收到，前进")
    if has_any(text, ("后退", "往后", "向后", "退后")):
        return Command("backward", "收到，后退")
    if has_any(text, ("左转", "向左")):
        return Command("left", "收到，左转")
    if has_any(text, ("右转", "向右")):
        return Command("right", "收到，右转")

    return None


def execute_command(robot: DogzillaVoiceRobot, command: Command, args: argparse.Namespace) -> bool:
    name = command.name
    if args.voice_reply and command.reply:
        robot.speak(command.reply)

    if name == "stop":
        robot.stop()
    elif name == "sit":
        robot.action(12, seconds=args.action_seconds)
    elif name == "handshake":
        robot.action(19, seconds=args.action_seconds)
    elif name == "stand":
        robot.action(2, seconds=2.0)
    elif name == "weather":
        try:
            report = fetch_weather_report(args, city=command.text)
        except Exception as exc:
            print("[WEATHER] error:", repr(exc), flush=True)
            report = "天气查询失败"
        robot.speak(report)
    elif name == "say":
        robot.speak(command.text)
    elif name == "time":
        now = datetime.datetime.now()
        robot.speak(f"现在是{now.hour}点{now.minute:02d}分。")
    elif name == "chat":
        try:
            answer = fetch_spark_answer(command.text, args)
        except Exception as exc:
            print("[SPARK] error:", repr(exc), flush=True)
            answer = "这个问题我暂时回答失败了"
        robot.speak(answer)
    elif name == "music_play":
        robot.speak("开始播放音乐")
        if robot.dry_run:
            print("[DRY] music play", flush=True)
            return True
        code = run_music_command("play", args)
        if code != 0:
            robot.speak("音乐播放失败")
    elif name == "music_stop":
        if robot.dry_run:
            print("[DRY] music stop", flush=True)
            return True
        code = run_music_command("stop", args)
        if code == 0:
            robot.speak("已停止播放音乐")
        else:
            robot.speak("停止音乐失败")
    elif name == "forward":
        robot.move_x(args.move_speed, args.move_seconds)
    elif name == "backward":
        robot.move_x(-args.move_speed, args.move_seconds)
    elif name == "left":
        robot.turn(args.turn_speed, args.turn_seconds)
    elif name == "right":
        robot.turn(-args.turn_speed, args.turn_seconds)
    else:
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DOGZILLA-Lite voice interaction with sit and handshake actions")
    parser.add_argument("--dry-run", action="store_true", help="print actions without controlling the robot")
    parser.add_argument("--once", action="store_true", help="listen once and exit")
    parser.add_argument("--mode", choices=("stream", "file"), default="stream")
    parser.add_argument("--voice-reply", action="store_true", help="speak command feedback; default is silent")
    parser.add_argument("--record-name", default=DEFAULT_RECORD_NAME)
    parser.add_argument("--record-seconds", type=int, default=2)
    parser.add_argument("--asr-timeout", type=float, default=12.0)
    parser.add_argument("--stream-window-seconds", type=float, default=10.0)
    parser.add_argument("--stream-rate", type=int, default=16000)
    parser.add_argument("--stream-format", default="S16_LE")
    parser.add_argument("--stream-device", default="")
    parser.add_argument("--stream-frame-ms", type=int, default=40)
    parser.add_argument("--stream-recv-timeout", type=float, default=0.02)
    parser.add_argument("--stream-vad-eos", type=int, default=800)
    parser.add_argument("--stream-debug", action="store_true")
    parser.add_argument(
        "--arecord-use-sudo",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="use sudo -n arecord first, then fall back to arecord",
    )
    parser.add_argument("--command-cooldown", type=float, default=0.8)
    parser.add_argument("--move-speed", type=int, default=12)
    parser.add_argument("--move-seconds", type=float, default=1.0)
    parser.add_argument("--turn-speed", type=int, default=45)
    parser.add_argument("--turn-seconds", type=float, default=0.7)
    parser.add_argument("--action-seconds", type=float, default=0.2)
    parser.add_argument("--music-player", default=str(DEFAULT_MUSIC_PLAYER))
    parser.add_argument("--music-dir", default=str(DEFAULT_DOGZILLA_MUSIC_DIR))
    parser.add_argument("--music-song", default="", help="song filename or keyword; empty means first song")
    parser.add_argument("--music-volume", type=int, default=85)
    parser.add_argument("--music-loop", action="store_true")
    parser.add_argument("--music-timeout", type=float, default=8.0)
    parser.add_argument("--weather-city", default=DEFAULT_WEATHER_CITY)
    parser.add_argument("--weather-city-label", default=DEFAULT_WEATHER_CITY_LABEL)
    parser.add_argument("--weather-lat", type=float, default=DEFAULT_WEATHER_LAT)
    parser.add_argument("--weather-lon", type=float, default=DEFAULT_WEATHER_LON)
    parser.add_argument("--weather-timeout", type=float, default=5.0)
    parser.add_argument(
        "--spark-chat",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="answer non-command speech with Spark Lite when SPARK_API_PASSWORD is set",
    )
    parser.add_argument("--spark-api-password", default=SPARK_API_PASSWORD)
    parser.add_argument("--spark-api-url", default=SPARK_API_URL)
    parser.add_argument("--spark-model", default=SPARK_MODEL)
    parser.add_argument("--spark-system-prompt", default=SPARK_SYSTEM_PROMPT)
    parser.add_argument("--spark-temperature", type=float, default=0.5)
    parser.add_argument("--spark-max-tokens", type=int, default=160)
    parser.add_argument("--spark-timeout", type=float, default=8.0)
    parser.add_argument("--spark-max-reply-chars", type=int, default=90)
    parser.add_argument("--spark-min-chars", type=int, default=3)
    parser.add_argument(
        "--spark-question-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="only send clear questions or directed requests to Spark; ordinary speech is ignored",
    )
    parser.add_argument("--tts-echo-ignore-seconds", type=float, default=5.0)
    parser.add_argument("--tts-echo-similarity", type=float, default=0.72)
    parser.add_argument("--same-text-cooldown", type=float, default=4.0)
    parser.add_argument("--same-text-similarity", type=float, default=0.88)
    parser.add_argument("--tts-backend", choices=("xunfei", "xgoedu"), default=os.getenv("DOGZILLA_TTS_BACKEND", "xunfei"))
    parser.add_argument("--tts-appid", default=XFYUN_TTS_APPID)
    parser.add_argument("--tts-api-key", default=XFYUN_TTS_API_KEY)
    parser.add_argument("--tts-api-secret", default=XFYUN_TTS_API_SECRET)
    parser.add_argument("--tts-voice", default=XFYUN_TTS_VCN)
    parser.add_argument("--tts-speed", type=int, default=XFYUN_TTS_SPEED)
    parser.add_argument("--tts-volume", type=int, default=XFYUN_TTS_VOLUME)
    parser.add_argument("--tts-pitch", type=int, default=XFYUN_TTS_PITCH)
    parser.add_argument("--tts-timeout", type=float, default=XFYUN_TTS_TIMEOUT)
    parser.add_argument("--appid", default=XFYUN_APPID)
    parser.add_argument("--api-key", default=XFYUN_API_KEY)
    parser.add_argument("--api-secret", default=XFYUN_API_SECRET)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    robot = DogzillaVoiceRobot(
        dry_run=args.dry_run,
        tts_backend=args.tts_backend,
        tts_appid=args.tts_appid,
        tts_api_key=args.tts_api_key,
        tts_api_secret=args.tts_api_secret,
        tts_voice=args.tts_voice,
        tts_speed=args.tts_speed,
        tts_volume=args.tts_volume,
        tts_pitch=args.tts_pitch,
        tts_timeout=args.tts_timeout,
    )
    recognizer = XunfeiIAT(args.appid, args.api_key, args.api_secret)

    if not recognizer.ready() and not args.dry_run:
        print("[ERROR] Missing Xunfei credentials.", flush=True)
        print("Set XFYUN_APPID, XFYUN_API_KEY, XFYUN_API_SECRET first.", flush=True)
        return 2

    robot.setup()
    print("[READY] continuous voice control is running", flush=True)
    last_command_name = ""
    last_command_at = 0.0
    last_handled_text = ""
    last_handled_at = 0.0

    try:
        while True:
            command = None
            text = ""

            if args.dry_run:
                text = input("Input recognized text: ")
                command = parse_command(text)
            elif args.mode == "stream":
                text, command = recognizer.stream_until_command(
                    parse_command,
                    rate=args.stream_rate,
                    fmt=args.stream_format,
                    device=args.stream_device,
                    frame_ms=args.stream_frame_ms,
                    window_seconds=args.stream_window_seconds,
                    recv_timeout=args.stream_recv_timeout,
                    vad_eos=args.stream_vad_eos,
                    use_sudo=args.arecord_use_sudo,
                    debug=args.stream_debug,
                )
            else:
                audio_file = robot.record(args.record_name, args.record_seconds)
                text = recognizer.recognize(audio_file, timeout=args.asr_timeout)
                command = parse_command(text)

            if text:
                print("[ASR]", text, flush=True)
            if is_recent_tts_echo(text, robot, args):
                print("[SKIP] tts echo:", text, flush=True)
                if args.once:
                    break
                continue
            if command is None and should_use_spark_chat(text, args):
                command = Command("chat", text=text)
            if command is not None:
                now = time.time()
                if (
                    command.name != "stop"
                    and is_recent_duplicate_text(text, last_handled_text, last_handled_at, args)
                ):
                    print("[SKIP] repeated speech:", text, flush=True)
                    if args.once:
                        break
                    continue
                if (
                    command.name == last_command_name
                    and now - last_command_at < args.command_cooldown
                ):
                    print("[SKIP] duplicate command:", command.name, flush=True)
                    if args.once:
                        break
                    continue
                execute_command(robot, command, args)
                last_command_name = command.name
                last_command_at = now
                last_handled_text = clean_text(text or command.text)
                last_handled_at = time.time()

            if args.once:
                break
    except KeyboardInterrupt:
        print("\n[EXIT] keyboard interrupt", flush=True)
    finally:
        robot.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
