#!/home/pi/RaspberryPi-CM5/xgovenv/bin/python
"""Minimal housekeeper controller: owner auth -> voice trigger -> grab and line follow."""

from __future__ import annotations

import argparse
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, NamedTuple

from echo_guard import EchoGuard
from expression_feedback import ExpressionDisplay
from housekeeper_config import ConfigError, HousekeeperConfig, load_config
from web_dashboard import DashboardServer, StatusBoard


ROBOT_PYTHON = Path("/home/pi/RaspberryPi-CM5/xgovenv/bin/python")
DEFAULT_ROBOT_IP = "172.20.10.9"
DEFAULT_MUSIC_PLAYER = Path("/home/pi/dogzilla_runs/dogzilla_music_player.py")
DEFAULT_MUSIC_DIR = Path("/home/pi/dogzilla_runs/music")
DEFAULT_SPARK_API_URL = "https://spark-api-open.xf-yun.com/v1/chat/completions"
DEFAULT_SPARK_MODEL = "lite"
DEFAULT_TTS_BACKEND = os.getenv("DOGZILLA_TTS_BACKEND", "xunfei")
DEFAULT_TTS_APPID = os.getenv("XFYUN_TTS_APPID", os.getenv("XFYUN_APPID", ""))
DEFAULT_TTS_API_KEY = os.getenv("XFYUN_TTS_API_KEY", os.getenv("XFYUN_API_KEY", ""))
DEFAULT_TTS_API_SECRET = os.getenv("XFYUN_TTS_API_SECRET", os.getenv("XFYUN_API_SECRET", ""))
DEFAULT_TTS_VOICE = os.getenv("XFYUN_TTS_VCN", "x4_xiaoyan")
DEFAULT_TTS_SPEED = int(os.getenv("XFYUN_TTS_SPEED", "50"))
DEFAULT_TTS_VOLUME = int(os.getenv("XFYUN_TTS_VOLUME", "70"))
DEFAULT_TTS_PITCH = int(os.getenv("XFYUN_TTS_PITCH", "50"))
DEFAULT_TTS_TIMEOUT = float(os.getenv("XFYUN_TTS_TIMEOUT", "10.0"))
DEFAULT_SPARK_SYSTEM_PROMPT = (
    "你是DOGZILLA Lite机器狗的语音助手。请用中文口语化回答，简短自然，适合直接朗读；"
    "一般不超过80个汉字。不要承诺播放音乐、闹钟、拍照、导航等未接入功能；"
    "除普通问答、背诗、讲故事、介绍知识外，目前可执行指令只有：坐下、握手、站起来、停止、前进、后退、左转、右转；"
    "问天气时需要用户说出城市。"
)
DEFAULT_QWEN_API_KEY = (
    os.getenv("QWEN_API_KEY")
    or os.getenv("DASHSCOPE_API_KEY")
    or os.getenv("BAILIAN_API_KEY")
    or ""
)
DEFAULT_QWEN_API_BASE = (
    os.getenv("QWEN_API_BASE")
    or os.getenv("DASHSCOPE_API_BASE")
    or os.getenv("BAILIAN_API_BASE")
    or "https://dashscope.aliyuncs.com/compatible-mode/v1"
).rstrip("/")
DEFAULT_QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-plus")
DEFAULT_QWEN_WEB_SEARCH = os.getenv("QWEN_WEB_SEARCH", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
DEFAULT_QWEN_SYSTEM_PROMPT = os.getenv(
    "QWEN_SYSTEM_PROMPT",
    "你是DOGZILLA Lite机器狗的语音助手。你运行在一只真实机器狗上。"
    "请用中文口语化回答，简短自然，适合直接朗读；一般不超过90个汉字。"
    "用户问实时信息、新闻、天气、比赛、价格、日期等内容时，要优先使用联网搜索结果。"
    "不要编造你不能控制的功能。机器狗本地已经能执行停止、坐下、握手、站起来、前进、后退、左转、右转等动作，"
    "这些动作由本地程序执行，你只负责其它问答、背诗、讲故事和知识解释。",
)

AUTH_RESULT_PATH = Path("/home/pi/xgoPictures/housekeeper/auth_result.json")
GRAB_RESULT_PATH = Path("/home/pi/xgoPictures/ball_grab/grab_result.json")

EXIT_AUTH_FAILED = 10
EXIT_NO_TASK = 11

TASK_TRIGGER_KEYWORDS = (
    "开始任务",
    "执行任务",
    "开始工作",
    "去捡球",
    "捡球",
    "抓球",
    "拿球",
    "拿红球",
    "找球",
    "送球",
)
# 兜底触发要求"动词+球"同时出现;动词表包含常见 ASR 同音误识别
BALL_TASK_VERB_CHARS = "捡拣检简见剑拿娜抓找送取提带"
TASK_OBJECT_KEYWORDS = ("球", "方块", "物品", "东西")
MOTION_ONLY_KEYWORDS = (
    "前进",
    "后退",
    "左转",
    "右转",
    "坐下",
    "握手",
    "站起来",
    "停止",
    "天气",
)
STOP_CONTROL_KEYWORDS = ("停止", "停下", "急停", "暂停", "别动")
STOP_NEGATION_PATTERNS = ("别停", "不要停", "不用停", "不停")
CONTINUE_CONTROL_KEYWORDS = ("继续", "恢复", "接着", "接着执行", "继续任务")
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


class HousekeeperTask(NamedTuple):
    name: str
    text: str = ""
    raw_text: str = ""
    requested_color: str = "red"
    effective_color: str = "red"
    target_station: str = "station_A"
    station_label: str = "客厅"
    color_label: str = "红球"
    color_downgraded: bool = False

    @classmethod
    def for_request(
        cls,
        *,
        raw_text: str,
        requested_color: str,
        effective_color: str,
        target_station: str,
        station_label: str,
        color_label: str,
        name: str = "grab_then_follow_line",
    ) -> "HousekeeperTask":
        return cls(
            name=name,
            text=raw_text,
            raw_text=raw_text,
            requested_color=requested_color,
            effective_color=effective_color,
            target_station=target_station,
            station_label=station_label,
            color_label=color_label,
            color_downgraded=requested_color != effective_color,
        )

    def summary(self) -> str:
        return "{} -> {}".format(self.color_label, self.station_label)

    def spoken_summary(self) -> str:
        return "收到,去拿{}送到{}".format(self.color_label, self.station_label)

    def capability_notice(self) -> str:
        if self.color_downgraded:
            return "我现在只会拿红球,先按红球执行"
        return ""


class VoiceEvent(NamedTuple):
    kind: str
    value: object
    text: str = ""


def clean_text(text: str) -> str:
    text = text.strip().lower()
    return re.sub(r"[\s，。！？、,.!?;；:：\"'“”‘’]", "", text)


def has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def parse_task_trigger(text: str, config: HousekeeperConfig | None = None) -> HousekeeperTask | None:
    config = config or HousekeeperConfig.default()
    cleaned = clean_text(text)
    if not cleaned:
        return None
    if cleaned in MOTION_ONLY_KEYWORDS:
        return None
    triggered = has_any(cleaned, TASK_TRIGGER_KEYWORDS)
    # 兜底:必须"动词+球"同时出现,避免"眼球""进球"这类闲聊误触发
    triggered = triggered or (
        any(keyword in cleaned for keyword in TASK_OBJECT_KEYWORDS)
        and any(ch in cleaned for ch in BALL_TASK_VERB_CHARS)
        and not has_any(cleaned, MOTION_ONLY_KEYWORDS)
    )
    if not triggered:
        return None
    requested_color = config.resolve_color(cleaned) or config.defaults.color
    effective_color = requested_color
    target_station = config.resolve_station(cleaned) or config.defaults.target_station
    return HousekeeperTask.for_request(
        raw_text=text,
        requested_color=requested_color,
        effective_color=effective_color,
        target_station=target_station,
        station_label=config.station_label(target_station),
        color_label=config.color_label(effective_color),
    )


def parse_control_command(text: str) -> str | None:
    cleaned = clean_text(text)
    if not cleaned:
        return None
    if has_any(cleaned, STOP_CONTROL_KEYWORDS) and not has_any(cleaned, STOP_NEGATION_PATTERNS):
        return "stop"
    if has_any(cleaned, CONTINUE_CONTROL_KEYWORDS):
        return "continue"
    return None


def parse_music_command(text: str) -> str | None:
    cleaned = clean_text(text)
    if not cleaned:
        return None
    if has_any(cleaned, MUSIC_STOP_KEYWORDS):
        return "stop"
    if has_any(cleaned, MUSIC_PLAY_KEYWORDS):
        return "play"
    return None


def parse_voice_event(text: str, config: HousekeeperConfig | None = None) -> VoiceEvent | None:
    music = parse_music_command(text)
    if music is not None:
        return VoiceEvent("music", music, text)
    control = parse_control_command(text)
    if control is not None:
        return VoiceEvent("control", control, text)
    task = parse_task_trigger(text, config)
    if task is not None:
        return VoiceEvent("task", task, text)
    return None


def answer_voice_chat(text: str, args: argparse.Namespace, speaker: object | None, *, voice_module=None) -> bool:
    if not text:
        return False
    if voice_module is None:
        import voice_interaction as voice_module

    if not voice_module.should_use_spark_chat(text, args):
        return False
    try:
        answer = voice_module.fetch_spark_answer(text, args)
    except Exception as exc:
        print("[CHAT] error: {!r}".format(exc), flush=True)
        answer = "这个问题我暂时回答失败了"
    speak(speaker, answer)
    return True


class ConsoleSpeaker:
    def speak(self, text: str) -> None:
        print("[SPEAK] {}".format(text), flush=True)


class TtsSpeaker:
    """TTS 播报,同时联动回声防护/表情/仪表盘(均可为 None)。"""

    def __init__(
        self,
        enabled: bool = True,
        echo_guard: EchoGuard | None = None,
        expressions: ExpressionDisplay | None = None,
        board: StatusBoard | None = None,
        tts_backend: str = DEFAULT_TTS_BACKEND,
        appid: str = DEFAULT_TTS_APPID,
        api_key: str = DEFAULT_TTS_API_KEY,
        api_secret: str = DEFAULT_TTS_API_SECRET,
        tts_voice: str = DEFAULT_TTS_VOICE,
        tts_speed: int = DEFAULT_TTS_SPEED,
        tts_volume: int = DEFAULT_TTS_VOLUME,
        tts_pitch: int = DEFAULT_TTS_PITCH,
        tts_timeout: float = DEFAULT_TTS_TIMEOUT,
        online_tts: object | None = None,
    ) -> None:
        self.enabled = enabled
        self.echo_guard = echo_guard
        self.expressions = expressions
        self.board = board
        self.tts_backend = tts_backend
        self.appid = appid
        self.api_key = api_key
        self.api_secret = api_secret
        self.tts_voice = tts_voice
        self.tts_speed = tts_speed
        self.tts_volume = tts_volume
        self.tts_pitch = tts_pitch
        self.tts_timeout = tts_timeout
        self._online_tts = online_tts
        self._edu = None

    def speak(self, text: str) -> None:
        print("[SPEAK] {}".format(text), flush=True)
        if self.board is not None:
            self.board.add_message(text, kind="speak")
        if self.echo_guard is not None:
            self.echo_guard.begin_speaking(text)
        try:
            if not self.enabled:
                return
            if self.tts_backend == "xunfei":
                try:
                    self._get_online_tts().speak(text)
                    return
                except Exception as exc:
                    print("[SPEAK] xunfei tts failed, fallback to xgoedu: {!r}".format(exc), flush=True)
            try:
                if self._edu is None:
                    from xgoedu import XGOEDU

                    self._edu = XGOEDU()
                self._edu.SpeechSynthesis(text)
            except Exception as exc:
                print("[SPEAK] tts failed: {!r}".format(exc), flush=True)
        finally:
            if self.echo_guard is not None:
                self.echo_guard.end_speaking(text)

    def _get_online_tts(self):
        if self._online_tts is None:
            import voice_interaction

            self._online_tts = voice_interaction.XunfeiTTS(
                self.appid,
                self.api_key,
                self.api_secret,
                vcn=self.tts_voice,
                speed=self.tts_speed,
                volume=self.tts_volume,
                pitch=self.tts_pitch,
                timeout=self.tts_timeout,
            )
        return self._online_tts

    def show(self, expression: str) -> None:
        if self.expressions is not None:
            self.expressions.show(expression)
        if self.board is not None:
            self.board.set_expression(expression)

    def set_stage(self, stage: str) -> None:
        if self.board is not None:
            self.board.set_stage(stage)


def speak(speaker: object | None, text: str) -> None:
    if speaker is None:
        ConsoleSpeaker().speak(text)
        return
    method = getattr(speaker, "speak", None)
    if callable(method):
        method(text)


def show_expression(speaker: object | None, expression: str) -> None:
    method = getattr(speaker, "show", None)
    if callable(method):
        method(expression)


def set_stage(speaker: object | None, stage: str) -> None:
    method = getattr(speaker, "set_stage", None)
    if callable(method):
        method(stage)


def default_housekeeper_dir() -> Path:
    return Path(__file__).resolve().parent


def build_child_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    pythonpath_parts = [
        "/home/pi/RaspberryPi-CM5/app",
        "/home/pi/RaspberryPi-CM5/demos",
        str(default_housekeeper_dir()),
    ]
    existing_pythonpath = env.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = ":".join(pythonpath_parts)
    env.setdefault("DISPLAY", ":0")
    return env


def build_face_auth_command(
    python_executable: Path,
    housekeeper_dir: Path,
    *,
    robot_ip: str,
    port: int,
    auth_result: Path = AUTH_RESULT_PATH,
    extra_args: list[str] | None = None,
) -> list[str]:
    command = [
        str(python_executable),
        "-u",
        str(housekeeper_dir / "face_interaction.py"),
        "--robot-ip",
        robot_ip,
        "--port",
        str(port),
        "--exit-on-owner",
        "--auth-result",
        str(auth_result),
    ]
    if extra_args:
        command.extend(extra_args)
    return command


def auth_result_confirms_owner(result_path: Path) -> bool:
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    return data.get("identity") == "owner"


def build_grab_workflow_command(
    python_executable: Path,
    housekeeper_dir: Path,
    *,
    line_seconds: float = 0,
    task: HousekeeperTask | None = None,
    return_home: bool = False,
    home_station: str = "home",
    return_timeout: float = 90.0,
    turn_home_speed: int = 20,
    turn_home_seconds: float = 2.4,
    line_result: Path | None = None,
    qr_decode_every_frames: int = 3,
    delivery_task_mode: str = "qr",
    extra_args: list[str] | None = None,
) -> list[str]:
    command = [
        str(python_executable),
        "-u",
        str(housekeeper_dir / "grab_then_follow_line.py"),
    ]
    if line_seconds > 0:
        command.extend(["--line-seconds", _format_seconds(line_seconds)])
    if task is not None:
        command.extend(["--target-color", task.effective_color])
        command.extend(["--target-station", task.target_station])
    if delivery_task_mode:
        command.extend(["--task-mode", delivery_task_mode])
    if line_result is not None:
        command.extend(["--line-result", str(line_result)])
    if qr_decode_every_frames > 0:
        command.extend(["--qr-decode-every-frames", str(qr_decode_every_frames)])
    if return_home:
        command.extend(
            [
                "--return-home",
                "--home-station",
                home_station,
                "--return-timeout",
                _format_seconds(return_timeout),
                "--turn-home-speed",
                str(turn_home_speed),
                "--turn-home-seconds",
                _format_seconds(turn_home_seconds),
            ]
        )
    if extra_args:
        command.extend(extra_args)
    return command


def build_music_player_command(
    python_executable: Path,
    player_script: Path,
    *,
    music_dir: Path = DEFAULT_MUSIC_DIR,
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


def run_music_command(
    action: str,
    args: argparse.Namespace,
    *,
    speaker: object | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> int:
    if action == "play":
        speak(speaker, "开始播放音乐")
    command = build_music_player_command(
        args.python,
        args.music_player,
        music_dir=args.music_dir,
        song=args.music_song,
        volume=args.music_volume,
        loop=args.music_loop,
        action=action,
    )
    print("\n=== MUSIC_{} ===".format(action.upper()), flush=True)
    print("COMMAND: {}".format(" ".join(command)), flush=True)
    try:
        result = runner(
            command,
            env=build_child_env(),
            timeout=args.music_timeout,
            check=False,
        )
    except Exception as exc:
        print("[MUSIC] failed: {!r}".format(exc), flush=True)
        speak(speaker, "音乐播放失败")
        return 1
    code = int(getattr(result, "returncode", 1))
    if action == "stop" and code == 0:
        speak(speaker, "已停止播放音乐")
    elif code != 0:
        speak(speaker, "音乐播放失败")
    return code


def _format_seconds(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def terminate_process(process: subprocess.Popen[str], *, grace_seconds: float = 3.0) -> bool:
    """终止子进程。返回 True 表示优雅退出(SIGINT),False 表示被强杀。

    子进程收到 SIGINT 后应自己把狗停住再退出(持有串口的一方负责停狗);
    只有强杀时,调用方才需要开串口兜底,并等串口空闲后再发。
    """
    if process.poll() is not None:
        return True
    try:
        process.send_signal(signal.SIGINT)
        process.wait(timeout=grace_seconds)
        return True
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return False


_stop_dog = None
_stop_dog_lock = threading.Lock()


def stop_robot_motion(*, serial_settle_seconds: float = 0.0) -> None:
    """父进程兜底停狗。串口实例缓存复用,避免反复开关串口。"""
    global _stop_dog
    if serial_settle_seconds > 0:
        time.sleep(serial_settle_seconds)
    try:
        from xgolib import XGO

        with _stop_dog_lock:
            if _stop_dog is None:
                _stop_dog = XGO(port="/dev/ttyAMA0", version="xgolite")
            for _ in range(4):
                _stop_dog.move("x", 0)
                _stop_dog.move("y", 0)
                _stop_dog.turn(0)
                _stop_dog.stop()
                time.sleep(0.04)
    except Exception as exc:
        _stop_dog = None
        print("[STOP] robot stop failed: {!r}".format(exc), flush=True)


class RuntimeController:
    def __init__(self, speaker: object | None = None) -> None:
        self.speaker = speaker
        self._lock = threading.Lock()
        self._paused = threading.Event()
        self._task_condition = threading.Condition()
        self._pending_task: HousekeeperTask | None = None
        self._current_process: subprocess.Popen[str] | None = None
        self._stop_generation = 0
        self._handled_stop_generation = 0

    def set_process(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            self._current_process = process

    def clear_process(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            if self._current_process is process:
                self._current_process = None

    def has_active_process(self) -> bool:
        with self._lock:
            return self._current_process is not None

    def request_stop(self) -> None:
        with self._lock:
            self._stop_generation += 1
            process = self._current_process
            already_paused = self._paused.is_set()
            self._paused.set()
        if not already_paused:
            speak(self.speaker, "已暂停")
            show_expression(self.speaker, "pause")
        graceful = True
        if process is not None:
            graceful = terminate_process(process)
        # 优雅退出时子进程已自己停狗;强杀时等 0.5s 串口空闲后再兜底
        stop_robot_motion(serial_settle_seconds=0.0 if graceful else 0.5)

    def request_continue(self) -> None:
        was_paused = self._paused.is_set()
        self._paused.clear()
        if was_paused:
            speak(self.speaker, "恢复任务")

    def request_task(self, task: HousekeeperTask) -> None:
        with self._task_condition:
            self._pending_task = task
            self._task_condition.notify_all()

    def wait_for_task(self, timeout_seconds: float) -> HousekeeperTask | None:
        deadline = time.time() + timeout_seconds
        with self._task_condition:
            while self._pending_task is None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return None
                self._task_condition.wait(timeout=remaining)
            task = self._pending_task
            self._pending_task = None
            return task

    def wait_while_paused(self) -> None:
        while self._paused.is_set():
            time.sleep(0.2)

    def mark_stop_handled(self) -> bool:
        with self._lock:
            if self._stop_generation <= self._handled_stop_generation:
                return False
            self._handled_stop_generation = self._stop_generation
            return True


class VoiceEventMonitor:
    def __init__(
        self,
        args: argparse.Namespace,
        controller: RuntimeController,
        echo_guard: EchoGuard | None = None,
        config: HousekeeperConfig | None = None,
    ) -> None:
        self.args = args
        self.controller = controller
        self.echo_guard = echo_guard
        self.config = config or HousekeeperConfig.default()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.args.disable_voice_control:
            print("[VOICE] disabled by argument", flush=True)
            return
        if not (self.args.appid and self.args.api_key and self.args.api_secret):
            print("[VOICE] missing Xunfei credentials; voice events disabled", flush=True)
            return
        if getattr(self.args, "spark_chat", True):
            import voice_interaction

            if not voice_interaction.chat_backend_available(self.args):
                print(
                    "[CHAT] missing QWEN_API_KEY or SPARK_API_PASSWORD; voice Q&A disabled",
                    flush=True,
                )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        import voice_interaction

        recognizer = voice_interaction.XunfeiIAT(
            self.args.appid,
            self.args.api_key,
            self.args.api_secret,
        )
        print("[VOICE] always-on listener started", flush=True)
        while not self._stop.is_set():
            try:
                text, event = recognizer.stream_until_command(
                    lambda text: parse_voice_event(text, self.config),
                    rate=self.args.stream_rate,
                    fmt=self.args.stream_format,
                    device=self.args.stream_device,
                    frame_ms=self.args.stream_frame_ms,
                    window_seconds=self.args.stream_window_seconds,
                    recv_timeout=self.args.stream_recv_timeout,
                    vad_eos=self.args.stream_vad_eos,
                    use_sudo=self.args.arecord_use_sudo,
                    debug=self.args.stream_debug,
                )
            except Exception as exc:
                print("[VOICE] listen error: {!r}".format(exc), flush=True)
                time.sleep(self.args.voice_retry_delay)
                continue
            if text:
                print("[ASR] {}".format(text), flush=True)
            if self.echo_guard is not None and self.echo_guard.is_echo(text):
                print("[VOICE] ignored self-echo: {}".format(text), flush=True)
                continue
            if event is None:
                if not should_answer_runtime_chat(self.controller):
                    print("[VOICE] ignored chat while workflow is active: {}".format(text), flush=True)
                    continue
                answer_voice_chat(
                    text,
                    self.args,
                    self.controller.speaker,
                    voice_module=voice_interaction,
                )
                continue
            if event.kind == "control" and event.value == "stop":
                self.controller.request_stop()
            elif event.kind == "control" and event.value == "continue":
                self.controller.request_continue()
            elif event.kind == "task" and isinstance(event.value, HousekeeperTask):
                print("Task heard from always-on voice: {}".format(event.value.summary()), flush=True)
                self.controller.request_task(event.value)
            elif event.kind == "music" and isinstance(event.value, str):
                run_music_command(event.value, self.args, speaker=self.controller.speaker)


VoiceControlMonitor = VoiceEventMonitor


def should_answer_runtime_chat(controller: RuntimeController | None) -> bool:
    return controller is None or not controller.has_active_process()


def wait_for_owner_auth(
    command: list[str],
    *,
    timeout_seconds: float,
    cwd: Path | None = None,
    auth_result: Path = AUTH_RESULT_PATH,
    controller: RuntimeController | None = None,
) -> bool:
    """结构化握手:人脸子进程确认主人后写结果文件并自己退出(exit 0)。

    成功条件 = 子进程退出码 0 且结果文件 identity == owner。
    子进程自己退出意味着摄像头已在其 finally 中确定释放,后续阶段可放心开摄像头。
    """
    print("\n=== FACE_AUTH ===", flush=True)
    print("COMMAND: {}".format(" ".join(command)), flush=True)
    try:
        auth_result.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
    process = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd is not None else None,
        env=build_child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if controller is not None:
        controller.set_process(process)
    selector = selectors.DefaultSelector()
    assert process.stdout is not None
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.time() + timeout_seconds
    try:
        while time.time() < deadline:
            events = selector.select(timeout=0.2)
            for key, _mask in events:
                line = key.fileobj.readline()
                if line:
                    print(line, end="", flush=True)
            if process.poll() is not None:
                confirmed = process.returncode == 0 and auth_result_confirms_owner(auth_result)
                if confirmed:
                    print("Owner confirmed. Face camera already released.", flush=True)
                else:
                    print("Face auth process exited without owner confirmation.", flush=True)
                return confirmed
        print("Face auth timed out.", flush=True)
        return False
    finally:
        selector.close()
        terminate_process(process)
        if controller is not None:
            controller.clear_process(process)


def wait_for_controller_task(controller: RuntimeController, timeout_seconds: float) -> HousekeeperTask | None:
    task = controller.wait_for_task(timeout_seconds)
    if task is None:
        print("Voice task timed out.", flush=True)
    else:
        print("Task confirmed from voice: {}".format(task.summary()), flush=True)
    return task


def wait_after_global_stop(controller: RuntimeController | None) -> bool:
    if controller is None:
        return False
    if not controller.mark_stop_handled():
        return False
    controller.wait_while_paused()
    return True


def wait_for_voice_task(args: argparse.Namespace) -> HousekeeperTask | None:
    config = getattr(args, "housekeeper_config", HousekeeperConfig.default())
    if args.dry_voice:
        text = input("Input task voice text: ")
        return parse_task_trigger(text, config)

    import voice_interaction

    recognizer = voice_interaction.XunfeiIAT(args.appid, args.api_key, args.api_secret)
    if not recognizer.ready():
        print("[ERROR] Missing Xunfei credentials for voice trigger.", flush=True)
        return None

    print("\n=== VOICE_TASK ===", flush=True)

    def listen_once() -> tuple[str, HousekeeperTask | None]:
        text, task = recognizer.stream_until_command(
            lambda text: parse_task_trigger(text, config),
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
        return text, task

    return wait_for_task_loop(
        listen_once,
        timeout_seconds=args.voice_timeout,
        retry_delay=args.voice_retry_delay,
    )


def wait_for_task_loop(
    listen_once: Callable[[], tuple[str, HousekeeperTask | None]],
    *,
    timeout_seconds: float,
    retry_delay: float,
    clock: Callable[[], float] = time.time,
    sleeper: Callable[[float], None] = time.sleep,
) -> HousekeeperTask | None:
    deadline = clock() + timeout_seconds
    while clock() < deadline:
        try:
            text, task = listen_once()
        except Exception as exc:
            print("[VOICE_TASK] listen error: {!r}".format(exc), flush=True)
            sleeper(max(0.0, retry_delay))
            continue
        if text:
            print("[ASR_TASK] {}".format(text), flush=True)
        if task is not None:
            print("Task confirmed from voice: {}".format(task.summary()), flush=True)
            return task
    print("Voice task timed out.", flush=True)
    return None


def workflow_feedback_for_line(line: str) -> str | None:
    if line.startswith("TASK_QR_SCAN_START"):
        prompt = line.removeprefix("TASK_QR_SCAN_START").strip()
        return prompt or "请把任务二维码放到摄像头前"
    if "=== GRAB ===" in line:
        return "开始抓球"
    if "Grab succeeded" in line:
        return "捡球成功"
    if "=== FIND_AND_ALIGN_LINE ===" in line:
        return "开始寻找黑线"
    if "Line alignment succeeded" in line or "=== FOLLOW_LINE ===" in line:
        return "开始巡线"
    return None


def augment_command_for_resume(command: list[str], result_path: Path = GRAB_RESULT_PATH) -> list[str]:
    """断点续跑:上次抓球已成功(球还在爪里)则跳过抓球阶段,直接找线巡线。"""
    if "--skip-grab" in command:
        return list(command)
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return list(command)
    if data.get("success"):
        return list(command) + ["--skip-grab"]
    return list(command)


def run_grab_workflow(
    command: list[str],
    *,
    cwd: Path | None = None,
    speaker: object | None = None,
    controller: RuntimeController | None = None,
    grab_result: Path = GRAB_RESULT_PATH,
) -> int:
    current_command = list(command)
    while True:
        if controller is not None:
            controller.wait_while_paused()
        code = _run_grab_workflow_once(current_command, cwd=cwd, speaker=speaker, controller=controller)
        if controller is not None and controller.mark_stop_handled():
            controller.wait_while_paused()
            resumed = augment_command_for_resume(command, grab_result)
            if "--skip-grab" in resumed and "--skip-grab" not in command:
                speak(speaker, "球还在爪里，直接继续找线")
            else:
                speak(speaker, "重新开始捡球任务")
            current_command = resumed
            continue
        return code


def _run_grab_workflow_once(
    command: list[str],
    *,
    cwd: Path | None = None,
    speaker: object | None = None,
    controller: RuntimeController | None = None,
) -> int:
    print("\n=== GRAB_THEN_FOLLOW_LINE ===", flush=True)
    print("COMMAND: {}".format(" ".join(command)), flush=True)
    process = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd is not None else None,
        env=build_child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if controller is not None:
        controller.set_process(process)
    spoken_messages: set[str] = set()
    try:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            message = workflow_feedback_for_line(line)
            if message and message not in spoken_messages:
                spoken_messages.add(message)
                speak(speaker, message)
        return process.wait()
    except KeyboardInterrupt:
        terminate_process(process)
        raise
    finally:
        if controller is not None:
            controller.clear_process(process)


def run_sequence(
    authenticate_owner: Callable[[], bool],
    listen_for_task: Callable[[], HousekeeperTask | None],
    run_task: Callable[[HousekeeperTask], int],
    speaker: object | None = None,
    controller: RuntimeController | None = None,
) -> int:
    while True:
        if controller is not None:
            controller.wait_while_paused()
        set_stage(speaker, "FACE_AUTH")
        show_expression(speaker, "scan")
        speak(speaker, "开始识别人脸")
        if authenticate_owner():
            break
        if wait_after_global_stop(controller):
            continue
        show_expression(speaker, "fail")
        speak(speaker, "人脸识别失败")
        return EXIT_AUTH_FAILED

    show_expression(speaker, "happy")
    speak(speaker, "人脸识别成功")

    while True:
        if controller is not None:
            controller.wait_while_paused()
        set_stage(speaker, "LISTEN")
        show_expression(speaker, "listen")
        speak(speaker, "开始听语音指令")
        task = listen_for_task()
        if task is not None:
            break
        if wait_after_global_stop(controller):
            continue
        show_expression(speaker, "fail")
        speak(speaker, "没有收到任务")
        return EXIT_NO_TASK

    while True:
        if controller is not None:
            controller.wait_while_paused()
        set_stage(speaker, "TASK")
        show_expression(speaker, "work")
        notice = task.capability_notice()
        if notice:
            speak(speaker, notice)
        speak(speaker, task.spoken_summary())
        speak(speaker, "开始执行捡球任务")
        code = run_task(task)
        if code != 0 and wait_after_global_stop(controller):
            continue
        break

    set_stage(speaker, "DONE")
    if code == 0:
        show_expression(speaker, "success")
        speak(speaker, "任务完成")
    else:
        show_expression(speaker, "fail")
        speak(speaker, "任务失败")
    return code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="智能管家犬第一版总控: 人脸 -> 语音 -> 抓球巡线")
    parser.add_argument("--housekeeper-dir", type=Path, default=default_housekeeper_dir())
    parser.add_argument("--config", type=Path, default=default_housekeeper_dir() / "housekeeper_config.json")
    parser.add_argument("--python", type=Path, default=ROBOT_PYTHON)
    parser.add_argument("--robot-ip", default=DEFAULT_ROBOT_IP)
    parser.add_argument("--face-port", type=int, default=8090)
    parser.add_argument("--face-timeout", type=float, default=60.0)
    parser.add_argument("--voice-timeout", type=float, default=120.0)
    parser.add_argument("--voice-retry-delay", type=float, default=1.0)
    parser.add_argument("--dry-voice", action="store_true", help="从终端输入模拟语音文本")
    parser.add_argument("--line-seconds", type=float, default=0.0)
    parser.add_argument("--delivery-task-mode", default="qr", help="传给 grab_then_follow_line.py 的任务模式")
    parser.add_argument("--disable-return-home", action="store_true", help="到站后不执行返航")
    parser.add_argument("--target-station", default="", help="调试用:覆盖语音解析出的目标站点")
    parser.add_argument("--target-color", default="", help="调试用:覆盖实际执行颜色")
    parser.add_argument("--face-arg", action="append", default=[], help="透传给 face_interaction.py")
    parser.add_argument("--grab-workflow-arg", action="append", default=[], help="透传给 grab_then_follow_line.py")
    parser.add_argument("--music-player", type=Path, default=DEFAULT_MUSIC_PLAYER)
    parser.add_argument("--music-dir", type=Path, default=DEFAULT_MUSIC_DIR)
    parser.add_argument("--music-song", default="", help="默认播放歌曲名或关键词；空表示播放目录第一首")
    parser.add_argument("--music-volume", type=int, default=85)
    parser.add_argument("--music-loop", action="store_true")
    parser.add_argument("--music-timeout", type=float, default=8.0)
    parser.add_argument("--stream-rate", type=int, default=16000)
    parser.add_argument("--stream-format", default="S16_LE")
    parser.add_argument("--stream-device", default="")
    parser.add_argument("--stream-frame-ms", type=int, default=40)
    parser.add_argument("--stream-window-seconds", type=float, default=10.0)
    parser.add_argument("--control-window-seconds", type=float, default=4.0)
    parser.add_argument("--stream-recv-timeout", type=float, default=0.02)
    parser.add_argument("--stream-vad-eos", type=int, default=800)
    parser.add_argument("--stream-debug", action="store_true")
    parser.add_argument(
        "--arecord-use-sudo",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--appid", default=os.getenv("XFYUN_APPID", ""))
    parser.add_argument("--api-key", default=os.getenv("XFYUN_API_KEY", ""))
    parser.add_argument("--api-secret", default=os.getenv("XFYUN_API_SECRET", ""))
    parser.add_argument(
        "--spark-chat",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="对非任务问句/请求启用语音问答；优先 Qwen，未配置时回退 Spark Lite",
    )
    parser.add_argument(
        "--qwen-chat",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="配置 QWEN_API_KEY 时优先使用 Qwen/Bailian 回答",
    )
    parser.add_argument("--qwen-api-key", default=DEFAULT_QWEN_API_KEY)
    parser.add_argument("--qwen-api-base", default=DEFAULT_QWEN_API_BASE)
    parser.add_argument("--qwen-model", default=DEFAULT_QWEN_MODEL)
    parser.add_argument("--qwen-system-prompt", default=DEFAULT_QWEN_SYSTEM_PROMPT)
    parser.add_argument("--qwen-temperature", type=float, default=0.45)
    parser.add_argument("--qwen-max-tokens", type=int, default=180)
    parser.add_argument("--qwen-timeout", type=float, default=10.0)
    parser.add_argument("--qwen-search-timeout", type=float, default=25.0)
    parser.add_argument("--qwen-max-reply-chars", type=int, default=110)
    parser.add_argument("--qwen-min-chars", type=int, default=2)
    parser.add_argument(
        "--qwen-web-search",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_QWEN_WEB_SEARCH,
        help="Qwen 回答时按需启用联网搜索",
    )
    parser.add_argument(
        "--qwen-always-search",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="强制每次 Qwen 回答都联网搜索",
    )
    parser.add_argument(
        "--qwen-question-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="只把明确问句或面向机器狗的请求发给 Qwen",
    )
    parser.add_argument("--spark-api-password", default=os.getenv("SPARK_API_PASSWORD", ""))
    parser.add_argument("--spark-api-url", default=os.getenv("SPARK_API_URL", DEFAULT_SPARK_API_URL))
    parser.add_argument("--spark-model", default=os.getenv("SPARK_MODEL", DEFAULT_SPARK_MODEL))
    parser.add_argument(
        "--spark-system-prompt",
        default=os.getenv("SPARK_SYSTEM_PROMPT", DEFAULT_SPARK_SYSTEM_PROMPT),
    )
    parser.add_argument("--spark-temperature", type=float, default=0.5)
    parser.add_argument("--spark-max-tokens", type=int, default=160)
    parser.add_argument("--spark-timeout", type=float, default=8.0)
    parser.add_argument("--spark-max-reply-chars", type=int, default=90)
    parser.add_argument("--spark-min-chars", type=int, default=3)
    parser.add_argument(
        "--spark-question-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="只把明确问句或面向机器狗的请求发给 Spark",
    )
    parser.add_argument(
        "--tts-backend",
        choices=("xunfei", "xgoedu"),
        default=DEFAULT_TTS_BACKEND,
        help="TTS 后端；xunfei 使用在线语音合成，失败时回退 xgoedu",
    )
    parser.add_argument("--tts-appid", default=DEFAULT_TTS_APPID)
    parser.add_argument("--tts-api-key", default=DEFAULT_TTS_API_KEY)
    parser.add_argument("--tts-api-secret", default=DEFAULT_TTS_API_SECRET)
    parser.add_argument("--tts-voice", default=DEFAULT_TTS_VOICE, help="讯飞在线语音合成发音人 vcn")
    parser.add_argument("--tts-speed", type=int, default=DEFAULT_TTS_SPEED)
    parser.add_argument("--tts-volume", type=int, default=DEFAULT_TTS_VOLUME)
    parser.add_argument("--tts-pitch", type=int, default=DEFAULT_TTS_PITCH)
    parser.add_argument("--tts-timeout", type=float, default=DEFAULT_TTS_TIMEOUT)
    parser.add_argument("--no-voice-feedback", action="store_true", help="只打印反馈，不播报")
    parser.add_argument("--disable-voice-control", action="store_true", help="禁用运行中停止/继续语音监听")
    parser.add_argument("--no-expressions", action="store_true", help="不在LCD上画表情")
    parser.add_argument("--dashboard-port", type=int, default=8091, help="电脑端仪表盘端口;0 表示禁用")
    return parser


def apply_task_overrides(
    task: HousekeeperTask,
    *,
    config: HousekeeperConfig,
    target_color: str = "",
    target_station: str = "",
) -> HousekeeperTask:
    effective_color = target_color or task.effective_color
    station = target_station or task.target_station
    return HousekeeperTask(
        name=task.name,
        text=task.text,
        raw_text=task.raw_text,
        requested_color=task.requested_color,
        effective_color=effective_color,
        target_station=station,
        station_label=config.station_label(station),
        color_label=config.color_label(effective_color),
        color_downgraded=task.requested_color != effective_color,
    )


def main() -> int:
    args = build_parser().parse_args()
    housekeeper_dir = args.housekeeper_dir
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print("[CONFIG] {}".format(exc), flush=True)
        return 2
    args.housekeeper_config = config

    face_command = build_face_auth_command(
        args.python,
        housekeeper_dir,
        robot_ip=args.robot_ip,
        port=args.face_port,
        extra_args=args.face_arg,
    )
    board = StatusBoard()
    dashboard = None
    if args.dashboard_port > 0:
        dashboard = DashboardServer(board, port=args.dashboard_port)
        dashboard.start()
        print("电脑端仪表盘: http://{}:{}/".format(args.robot_ip, args.dashboard_port), flush=True)

    echo_guard = EchoGuard()
    expressions = ExpressionDisplay(enabled=not args.no_expressions)
    speaker = TtsSpeaker(
        enabled=not args.no_voice_feedback,
        echo_guard=echo_guard,
        expressions=expressions,
        board=board,
        tts_backend=args.tts_backend,
        appid=args.tts_appid,
        api_key=args.tts_api_key,
        api_secret=args.tts_api_secret,
        tts_voice=args.tts_voice,
        tts_speed=args.tts_speed,
        tts_volume=args.tts_volume,
        tts_pitch=args.tts_pitch,
        tts_timeout=args.tts_timeout,
    )
    controller = RuntimeController(speaker=speaker)
    monitor = VoiceEventMonitor(args, controller, echo_guard=echo_guard, config=config)

    def run_task(task: HousekeeperTask) -> int:
        task = apply_task_overrides(
            task,
            config=config,
            target_color=args.target_color,
            target_station=args.target_station,
        )
        board.set_task(task.summary())
        grab_command = build_grab_workflow_command(
            args.python,
            housekeeper_dir,
            line_seconds=args.line_seconds,
            task=task,
            return_home=config.return_home.enabled and not args.disable_return_home,
            return_timeout=config.return_home.timeout_seconds,
            turn_home_speed=config.return_home.turn_speed,
            turn_home_seconds=config.return_home.turn_seconds,
            line_result=Path(config.line.result_path),
            qr_decode_every_frames=config.line.qr_decode_every_frames,
            delivery_task_mode=args.delivery_task_mode,
            extra_args=args.grab_workflow_arg,
        )
        return run_grab_workflow(
            grab_command,
            cwd=housekeeper_dir,
            speaker=speaker,
            controller=controller,
        )

    if not args.dry_voice:
        monitor.start()
    try:
        return run_sequence(
            lambda: wait_for_owner_auth(
                face_command,
                timeout_seconds=args.face_timeout,
                cwd=housekeeper_dir,
                controller=controller,
            ),
            lambda: wait_for_voice_task(args) if args.dry_voice else wait_for_controller_task(controller, args.voice_timeout),
            run_task,
            speaker=speaker,
            controller=controller,
        )
    finally:
        monitor.stop()
        if dashboard is not None:
            dashboard.stop()


if __name__ == "__main__":
    raise SystemExit(main())
