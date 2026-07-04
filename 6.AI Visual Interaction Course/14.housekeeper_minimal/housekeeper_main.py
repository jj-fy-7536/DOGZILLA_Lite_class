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

from chat_config import resolve_chat_defaults
from echo_guard import EchoGuard
from expression_feedback import ExpressionDisplay
from housekeeper_config import ConfigError, HousekeeperConfig, load_config
from web_dashboard import DashboardServer, StatusBoard


ROBOT_PYTHON = Path("/home/pi/RaspberryPi-CM5/xgovenv/bin/python")
DEFAULT_ROBOT_IP = "172.20.10.9"
DEFAULT_MUSIC_PLAYER = Path("/home/pi/dogzilla_runs/dogzilla_music_player.py")
DEFAULT_MUSIC_DIR = Path("/home/pi/dogzilla_runs/music")
CHAT_DEFAULTS = resolve_chat_defaults()
DEFAULT_CHAT_API_KEY = CHAT_DEFAULTS.api_key
DEFAULT_SPARK_API_URL = CHAT_DEFAULTS.api_url
DEFAULT_SPARK_MODEL = CHAT_DEFAULTS.model
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
    "你是DOGZILLA Lite机器狗的语音助手。用中文口语化回答，简短自然，像正常聊天，适合直接朗读；"
    "一般不超过80个汉字。你可以回答常识、解释概念、背诗、讲故事、天气、比赛、新闻和国际形势；"
    "如果提示里提供了实时网页搜索结果，必须优先根据搜索结果回答，不要说自己没有实时搜索、没有数据库或不能联网；"
    "如果搜索结果不足，就说没搜到足够信息，并结合背景知识简短回答，不要叫用户自己去联网查。"
    "不要承诺闹钟、拍照、导航等未接入功能；机器人动作指令有：坐下、握手、站起来、停止、前进、后退、左转、右转；"
    "听不清或语义不完整时，只简短追问一句，不要罗列功能菜单。"
)

AUTH_RESULT_PATH = Path("/home/pi/xgoPictures/housekeeper/auth_result.json")
GRAB_RESULT_PATH = Path("/home/pi/xgoPictures/ball_grab/grab_result.json")

EXIT_AUTH_FAILED = 10
EXIT_NO_TASK = 11
EXIT_STOPPED = 12

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
TASK_STOP_PROCESS_PATTERNS = (
    "grab_then_follow_line.py",
    "ball_grab_v3.py",
    "find_and_align_line.py",
    "follow_line.py",
)


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


def add_status_message(speaker: object | None, text: str, kind: str = "info") -> None:
    if not text:
        return
    board = getattr(speaker, "board", None)
    if board is not None:
        board.add_message(text, kind=kind)


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


def stop_music_silent(
    args: argparse.Namespace,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> int:
    command = build_music_player_command(
        args.python,
        args.music_player,
        music_dir=args.music_dir,
        song=args.music_song,
        volume=args.music_volume,
        loop=args.music_loop,
        action="stop",
    )
    print("\n=== MUSIC_STOP_SILENT ===", flush=True)
    print("COMMAND: {}".format(" ".join(command)), flush=True)
    try:
        result = runner(command, env=build_child_env(), timeout=args.music_timeout, check=False)
    except Exception as exc:
        print("[MUSIC] silent stop failed: {!r}".format(exc), flush=True)
        return 1
    return int(getattr(result, "returncode", 1))


def _format_seconds(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _send_process_tree_signal(process: subprocess.Popen[str], sig: int) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        return
    except Exception:
        process.send_signal(sig)


def terminate_process(process: subprocess.Popen[str], *, grace_seconds: float = 3.0) -> bool:
    """终止子进程。返回 True 表示优雅退出(SIGINT),False 表示被强杀。

    子进程收到 SIGINT 后应自己把狗停住再退出(持有串口的一方负责停狗);
    只有强杀时,调用方才需要开串口兜底,并等串口空闲后再发。
    """
    if process.poll() is not None:
        return True
    try:
        _send_process_tree_signal(process, signal.SIGINT)
        process.wait(timeout=grace_seconds)
        return True
    except subprocess.TimeoutExpired:
        _send_process_tree_signal(process, signal.SIGTERM)
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            _send_process_tree_signal(process, signal.SIGKILL)
            process.wait()
        return False


_stop_dog = None
_stop_dog_lock = threading.Lock()


def stop_robot_motion(*, serial_settle_seconds: float = 0.0, cycles: int = 4) -> None:
    """父进程兜底停狗。串口实例缓存复用,避免反复开关串口。"""
    global _stop_dog
    if serial_settle_seconds > 0:
        time.sleep(serial_settle_seconds)
    try:
        from xgolib import XGO

        with _stop_dog_lock:
            if _stop_dog is None:
                _stop_dog = XGO(port="/dev/ttyAMA0", version="xgolite")
            for _ in range(max(1, int(cycles))):
                _stop_dog.move("x", 0)
                _stop_dog.move("y", 0)
                _stop_dog.turn(0)
                _stop_dog.stop()
                time.sleep(0.04)
    except Exception as exc:
        _stop_dog = None
        print("[STOP] robot stop failed: {!r}".format(exc), flush=True)


def _pgrep(pattern: str) -> list[int]:
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        print("[STOP] pgrep failed for {}: {!r}".format(pattern, exc), flush=True)
        return []
    pids = []
    current_pid = os.getpid()
    for token in result.stdout.split():
        try:
            pid = int(token)
        except ValueError:
            continue
        if pid != current_pid:
            pids.append(pid)
    return pids


def kill_task_subprocesses(*, grace_seconds: float = 1.0) -> int:
    """兜底清理抓球/巡线残留进程,用于网页按钮和语音急停。"""
    killed: set[int] = set()
    for pattern in TASK_STOP_PROCESS_PATTERNS:
        for pid in _pgrep(pattern):
            try:
                print("[STOP] TERM {} pid {}".format(pattern, pid), flush=True)
                os.kill(pid, signal.SIGTERM)
                killed.add(pid)
            except ProcessLookupError:
                pass
            except Exception as exc:
                print("[STOP] TERM failed pid {}: {!r}".format(pid, exc), flush=True)
    if killed and grace_seconds > 0:
        time.sleep(grace_seconds)
    for pattern in TASK_STOP_PROCESS_PATTERNS:
        for pid in _pgrep(pattern):
            try:
                print("[STOP] KILL {} pid {}".format(pattern, pid), flush=True)
                os.kill(pid, signal.SIGKILL)
                killed.add(pid)
            except ProcessLookupError:
                pass
            except Exception as exc:
                print("[STOP] KILL failed pid {}: {!r}".format(pid, exc), flush=True)
    return len(killed)


def emergency_stop_everything(args: argparse.Namespace, controller: "RuntimeController") -> None:
    """和外部手动“停止”一致:停任务、停音乐、兜底停狗。"""
    controller.request_stop(speak_feedback=False)
    kill_task_subprocesses()
    stop_music_silent(args)
    stop_robot_motion(cycles=8)


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

    def request_stop(self, *, speak_feedback: bool = True) -> None:
        with self._lock:
            self._stop_generation += 1
            process = self._current_process
            self._paused.clear()
        with self._task_condition:
            self._pending_task = None
            self._task_condition.notify_all()
        if speak_feedback:
            speak(self.speaker, "已停止")
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

    def has_active_process(self) -> bool:
        with self._lock:
            return self._current_process is not None and self._current_process.poll() is None

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

    def stop_generation(self) -> int:
        with self._lock:
            return self._stop_generation

    def mark_stop_handled(self, *, since_generation: int = 0) -> bool:
        with self._lock:
            handled_generation = max(self._handled_stop_generation, since_generation)
            if self._stop_generation <= handled_generation:
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
        self._dog = None

    def _parse_stream_event(self, text: str) -> VoiceEvent | None:
        event = parse_voice_event(text, self.config)
        if event is not None:
            return event
        import voice_interaction

        command = voice_interaction.parse_command(text)
        if command is None or command.name in ("music_play", "music_stop", "chat"):
            return None
        return VoiceEvent("builtin", command, text)

    def start(self) -> None:
        if self.args.disable_voice_control:
            print("[VOICE] disabled by argument", flush=True)
            return
        if not (self.args.appid and self.args.api_key and self.args.api_secret):
            print("[VOICE] missing Xunfei credentials; voice events disabled", flush=True)
            return
        if getattr(self.args, "spark_chat", True) and not getattr(self.args, "spark_api_password", ""):
            print("[CHAT] missing DEEPSEEK_API_KEY or SPARK_API_PASSWORD; voice Q&A disabled", flush=True)
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
                    self._parse_stream_event,
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
                add_status_message(self.controller.speaker, text, kind="asr")
            if self.echo_guard is not None and self.echo_guard.is_echo(text):
                print("[VOICE] ignored self-echo: {}".format(text), flush=True)
                add_status_message(self.controller.speaker, "忽略回声: {}".format(text), kind="skip")
                continue
            if self.controller.has_active_process():
                if event is not None and event.kind == "control" and event.value == "stop":
                    self._stop_everything("语音停止")
                else:
                    add_status_message(self.controller.speaker, "任务中静默: {}".format(text), kind="skip")
                continue
            if event is None:
                if self._execute_builtin_voice_command(voice_interaction, text):
                    continue
                handled = answer_voice_chat(
                    text,
                    self.args,
                    self.controller.speaker,
                    voice_module=voice_interaction,
                )
                if not handled:
                    add_status_message(self.controller.speaker, "噪音过滤: {}".format(text), kind="skip")
                continue
            if event.kind == "control" and event.value == "stop":
                self._stop_everything("语音停止")
            elif event.kind == "control" and event.value == "continue":
                add_status_message(self.controller.speaker, "继续任务", kind="event")
                self.controller.request_continue()
            elif event.kind == "task" and isinstance(event.value, HousekeeperTask):
                print("Task heard from always-on voice: {}".format(event.value.summary()), flush=True)
                add_status_message(self.controller.speaker, "任务: {}".format(event.value.summary()), kind="event")
                self.controller.request_task(event.value)
            elif event.kind == "music" and isinstance(event.value, str):
                add_status_message(self.controller.speaker, "音乐: {}".format(event.value), kind="event")
                run_music_command(event.value, self.args, speaker=self.controller.speaker)
            elif event.kind == "builtin":
                self._execute_builtin_voice_command(voice_interaction, text, command=event.value)

    def _stop_everything(self, source: str) -> None:
        add_status_message(self.controller.speaker, "{}: 停止所有任务".format(source), kind="event")
        emergency_stop_everything(self.args, self.controller)

    def _execute_builtin_voice_command(self, voice_module, text: str, command=None) -> bool:
        command = command or voice_module.parse_command(text)
        if command is None:
            return False
        name = command.name
        if name in ("music_play", "music_stop", "chat"):
            return False
        if self.controller.has_active_process() and name != "stop":
            add_status_message(self.controller.speaker, "任务中静默: {}".format(text), kind="skip")
            return True
        add_status_message(self.controller.speaker, "命令: {}".format(name), kind="event")
        if name == "stop":
            self._stop_everything("语音停止")
        elif command.reply:
            speak(self.controller.speaker, command.reply)
        if name == "stop":
            return True
        if name == "sit":
            self._run_action(12, self.args.action_seconds)
        elif name == "handshake":
            self._run_action(19, self.args.action_seconds)
        elif name == "stand":
            self._run_action(2, 2.0)
        elif name == "say":
            speak(self.controller.speaker, command.text)
        elif name == "time":
            now = time.localtime()
            speak(self.controller.speaker, "现在是{}点{:02d}分。".format(now.tm_hour, now.tm_min))
        elif name == "forward":
            self._move_x(self.args.move_speed, self.args.move_seconds)
        elif name == "backward":
            self._move_x(-self.args.move_speed, self.args.move_seconds)
        elif name == "left":
            self._turn(self.args.move_speed, self.args.move_seconds)
        elif name == "right":
            self._turn(-self.args.move_speed, self.args.move_seconds)
        else:
            return False
        return True

    def _get_dog(self):
        if self._dog is None:
            from xgolib import XGO

            try:
                self._dog = XGO(port="/dev/ttyAMA0", version="xgolite")
            except TypeError:
                self._dog = XGO("xgolite")
        return self._dog

    def _run_action(self, action_id: int, seconds: float) -> None:
        if self.controller.has_active_process():
            speak(self.controller.speaker, "任务执行中，先说停止再控制动作")
            return
        try:
            self._stop_motion()
            self._get_dog().action(action_id)
            time.sleep(seconds)
        except Exception as exc:
            print("[VOICE_ACTION] failed: {!r}".format(exc), flush=True)
            speak(self.controller.speaker, "动作执行失败")

    def _move_x(self, speed: int, seconds: float) -> None:
        if self.controller.has_active_process():
            speak(self.controller.speaker, "任务执行中，先说停止再控制动作")
            return
        try:
            self._get_dog().move("x", int(speed))
            time.sleep(seconds)
            self._stop_motion()
        except Exception as exc:
            print("[VOICE_MOVE] failed: {!r}".format(exc), flush=True)
            speak(self.controller.speaker, "动作执行失败")

    def _turn(self, speed: int, seconds: float) -> None:
        if self.controller.has_active_process():
            speak(self.controller.speaker, "任务执行中，先说停止再控制动作")
            return
        try:
            self._get_dog().turn(int(speed))
            time.sleep(seconds)
            self._stop_motion()
        except Exception as exc:
            print("[VOICE_TURN] failed: {!r}".format(exc), flush=True)
            speak(self.controller.speaker, "动作执行失败")

    def _stop_motion(self) -> None:
        dog = self._get_dog()
        for _ in range(4):
            dog.move("x", 0)
            dog.move("y", 0)
            dog.turn(0)
            dog.stop()
            time.sleep(0.04)


VoiceControlMonitor = VoiceEventMonitor


def wait_for_owner_auth(
    command: list[str],
    *,
    timeout_seconds: float,
    cwd: Path | None = None,
    auth_result: Path = AUTH_RESULT_PATH,
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
        start_new_session=True,
    )
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


def wait_for_controller_task(controller: RuntimeController, timeout_seconds: float) -> HousekeeperTask | None:
    task = controller.wait_for_task(timeout_seconds)
    if task is None:
        print("Voice task timed out.", flush=True)
    else:
        print("Task confirmed from voice: {}".format(task.summary()), flush=True)
    return task


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
    start_stop_generation = controller.stop_generation() if controller is not None else 0
    if controller is not None:
        controller.wait_while_paused()
    code = _run_grab_workflow_once(command, cwd=cwd, speaker=speaker, controller=controller)
    if controller is not None and controller.mark_stop_handled(since_generation=start_stop_generation):
        return EXIT_STOPPED
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
        start_new_session=True,
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
                if speaker is not None:
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
    quiet_during_task: bool = False,
) -> int:
    set_stage(speaker, "FACE_AUTH")
    show_expression(speaker, "scan")
    speak(speaker, "开始识别人脸")
    if not authenticate_owner():
        show_expression(speaker, "fail")
        speak(speaker, "人脸识别失败")
        return EXIT_AUTH_FAILED
    show_expression(speaker, "happy")
    speak(speaker, "人脸识别成功")
    listen_announced = False
    while True:
        set_stage(speaker, "LISTEN")
        show_expression(speaker, "listen")
        if listen_announced:
            add_status_message(speaker, "等待新任务", kind="event")
        else:
            speak(speaker, "开始听语音指令")
            listen_announced = True
        task = listen_for_task()
        if task is None:
            show_expression(speaker, "fail")
            speak(speaker, "没有收到任务")
            return EXIT_NO_TASK
        set_stage(speaker, "TASK")
        show_expression(speaker, "work")
        notice = task.capability_notice()
        if quiet_during_task:
            add_status_message(speaker, "开始执行任务: {}".format(task.summary()), kind="event")
        else:
            if notice:
                speak(speaker, notice)
            speak(speaker, task.spoken_summary())
            speak(speaker, "开始执行捡球任务")
        code = run_task(task)
        if code == EXIT_STOPPED:
            show_expression(speaker, "listen")
            add_status_message(speaker, "任务已停止，等待新任务", kind="event")
            continue
        set_stage(speaker, "DONE")
        if code == 0:
            show_expression(speaker, "success")
            if quiet_during_task:
                add_status_message(speaker, "任务完成", kind="event")
            else:
                speak(speaker, "任务完成")
        else:
            show_expression(speaker, "fail")
            if quiet_during_task:
                add_status_message(speaker, "任务失败", kind="event")
            else:
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
    parser.add_argument("--disable-return-home", action="store_true", help="到站后不执行返航")
    parser.add_argument("--target-station", default="", help="调试用:覆盖语音解析出的目标站点")
    parser.add_argument("--target-color", default="", help="调试用:覆盖实际执行颜色")
    parser.add_argument("--face-arg", action="append", default=[], help="透传给 face_interaction.py")
    parser.add_argument("--grab-workflow-arg", action="append", default=[], help="透传给 grab_then_follow_line.py")
    parser.add_argument(
        "--quiet-during-task",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="执行抓球/巡线任务时不语音回答，只在仪表盘记录",
    )
    parser.add_argument("--music-player", type=Path, default=DEFAULT_MUSIC_PLAYER)
    parser.add_argument("--music-dir", type=Path, default=DEFAULT_MUSIC_DIR)
    parser.add_argument("--music-song", default="", help="默认播放歌曲名或关键词；空表示播放目录第一首")
    parser.add_argument("--music-volume", type=int, default=85)
    parser.add_argument("--music-loop", action="store_true")
    parser.add_argument("--music-timeout", type=float, default=8.0)
    parser.add_argument("--voice-reply", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--action-seconds", type=float, default=3.0)
    parser.add_argument("--move-speed", type=int, default=12)
    parser.add_argument("--move-seconds", type=float, default=1.2)
    parser.add_argument("--weather-city", default=os.getenv("DOGZILLA_WEATHER_CITY", ""))
    parser.add_argument("--weather-city-label", default=os.getenv("DOGZILLA_WEATHER_CITY_LABEL", ""))
    parser.add_argument("--weather-lat", type=float, default=os.getenv("DOGZILLA_WEATHER_LAT"))
    parser.add_argument("--weather-lon", type=float, default=os.getenv("DOGZILLA_WEATHER_LON"))
    parser.add_argument("--weather-timeout", type=float, default=5.0)
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
        help="对非任务问句/请求启用 DeepSeek 或 Spark 问答",
    )
    parser.add_argument("--spark-api-password", default=DEFAULT_CHAT_API_KEY)
    parser.add_argument("--spark-api-url", default=DEFAULT_SPARK_API_URL)
    parser.add_argument("--spark-model", default=DEFAULT_SPARK_MODEL)
    parser.add_argument("--deepseek-api-key", dest="spark_api_password", default=argparse.SUPPRESS)
    parser.add_argument("--deepseek-api-url", dest="spark_api_url", default=argparse.SUPPRESS)
    parser.add_argument("--deepseek-model", dest="spark_model", default=argparse.SUPPRESS)
    parser.add_argument(
        "--spark-system-prompt",
        default=os.getenv("SPARK_SYSTEM_PROMPT", DEFAULT_SPARK_SYSTEM_PROMPT),
    )
    parser.add_argument("--spark-temperature", type=float, default=0.5)
    parser.add_argument("--spark-max-tokens", type=int, default=320)
    parser.add_argument("--spark-timeout", type=float, default=15.0)
    parser.add_argument("--spark-max-reply-chars", type=int, default=160)
    parser.add_argument("--spark-min-chars", type=int, default=3)
    parser.add_argument("--web-search", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--web-search-url", default=os.getenv("DOGZILLA_WEB_SEARCH_URL", "auto"))
    parser.add_argument("--web-search-timeout", type=float, default=float(os.getenv("DOGZILLA_WEB_SEARCH_TIMEOUT", "8.0")))
    parser.add_argument("--web-search-max-chars", type=int, default=int(os.getenv("DOGZILLA_WEB_SEARCH_MAX_CHARS", "1800")))
    parser.add_argument(
        "--spark-question-only",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="只把明确问句或面向机器狗的请求发给 AI 问答",
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

    def dashboard_stop() -> None:
        board.add_message("网页按钮: 停止所有任务", kind="event")
        emergency_stop_everything(args, controller)

    if args.dashboard_port > 0:
        dashboard = DashboardServer(board, port=args.dashboard_port, on_stop=dashboard_stop)
        dashboard.start()
        print("电脑端仪表盘: http://{}:{}/".format(args.robot_ip, args.dashboard_port), flush=True)

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
            extra_args=args.grab_workflow_arg,
        )
        return run_grab_workflow(
            grab_command,
            cwd=housekeeper_dir,
            speaker=None if args.quiet_during_task else speaker,
            controller=controller,
        )

    if not args.dry_voice:
        monitor.start()
    try:
        return run_sequence(
            lambda: wait_for_owner_auth(face_command, timeout_seconds=args.face_timeout, cwd=housekeeper_dir),
            lambda: wait_for_voice_task(args) if args.dry_voice else wait_for_controller_task(controller, args.voice_timeout),
            run_task,
            speaker=speaker,
            quiet_during_task=args.quiet_during_task,
        )
    finally:
        monitor.stop()
        if dashboard is not None:
            dashboard.stop()


if __name__ == "__main__":
    raise SystemExit(main())
