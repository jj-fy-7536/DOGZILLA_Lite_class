"""Shared music + dance helpers for voice and housekeeper entry points."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable


DEFAULT_MUSIC_PLAYER = Path(
    os.getenv("DOGZILLA_MUSIC_PLAYER", "/home/pi/dogzilla_runs/dogzilla_music_player.py")
)
DEFAULT_MUSIC_DIR = Path(os.getenv("DOGZILLA_MUSIC_DIR", "/home/pi/dogzilla_runs/music"))
DEFAULT_MUSIC_DANCE_PID = Path(
    os.getenv("DOGZILLA_MUSIC_DANCE_PID", "/home/pi/dogzilla_runs/music_dance.pid")
)

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
MUSIC_DANCE_PLAY_KEYWORDS = (
    "放歌跳舞",
    "边听边跳",
    "跳舞放歌",
    "边唱边跳",
    "音乐跳舞",
    "听歌跳舞",
)
MUSIC_STOP_KEYWORDS = (
    "停歌",
    "停止播放",
    "停止音乐",
    "关音乐",
    "关闭音乐",
    "别放了",
    "停止跳舞",
    "别跳了",
    "不要跳了",
)
SUPPORTED_MUSIC_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


def clean_text(text: str) -> str:
    text = text.strip().lower()
    return re.sub(r"[\s，。！？、,.!?;；:：\"'“”‘’]", "", text)


def has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def default_music_dance_script() -> Path:
    deployed = Path(
        os.getenv("DOGZILLA_MUSIC_DANCE_SCRIPT", "/home/pi/dogzilla_runs/dogzilla_music_dance.py")
    )
    bundled = Path(__file__).resolve().with_name("dogzilla_music_dance.py")
    if deployed.exists():
        return deployed
    return bundled


def parse_music_command(text: str) -> str | None:
    cleaned = clean_text(text)
    if not cleaned:
        return None
    if has_any(cleaned, MUSIC_STOP_KEYWORDS):
        return "stop"
    if has_any(cleaned, MUSIC_DANCE_PLAY_KEYWORDS):
        return "dance"
    if has_any(cleaned, MUSIC_PLAY_KEYWORDS):
        return "play"
    return None


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


def build_music_dance_command(
    python_executable: Path,
    dance_script: Path,
    *,
    music_player: Path = DEFAULT_MUSIC_PLAYER,
    music_dir: Path = DEFAULT_MUSIC_DIR,
    song: str = "",
    volume: int = 85,
    loop: bool = False,
    pid_file: Path = DEFAULT_MUSIC_DANCE_PID,
    action: str = "play",
    dance_actions: str = "23,16,15",
    action_seconds: float = 3.0,
) -> list[str]:
    if action == "stop":
        return [str(python_executable), str(dance_script), "--stop", "--pid-file", str(pid_file)]
    command = [
        str(python_executable),
        str(dance_script),
        "--background",
        "--music-player",
        str(music_player),
        "--music-dir",
        str(music_dir),
        "--volume",
        str(max(0, min(100, int(volume)))),
        "--pid-file",
        str(pid_file),
        "--dance-actions",
        dance_actions,
        "--action-seconds",
        str(action_seconds),
    ]
    resolved_song = song or first_music_song_name(music_dir)
    if resolved_song:
        command.extend(["--song", resolved_song])
    if loop:
        command.append("--loop")
    return command


def read_pid_file(pid_file: Path) -> int | None:
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def terminate_pid(pid: int, *, grace_seconds: float = 3.0) -> bool:
    try:
        os.kill(pid, signal.SIGINT)
        deadline = time.time() + grace_seconds
        while time.time() < deadline:
            if not process_alive(pid):
                return True
            time.sleep(0.1)
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.2)
        if process_alive(pid):
            os.kill(pid, signal.SIGKILL)
        return False
    except ProcessLookupError:
        return True


def stop_music_dance_daemon(
    args: object,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> int:
    pid_file = Path(getattr(args, "music_dance_pid", DEFAULT_MUSIC_DANCE_PID))
    pid = read_pid_file(pid_file)
    if pid is not None:
        terminate_pid(pid)
    try:
        pid_file.unlink()
    except FileNotFoundError:
        pass

    python_executable = Path(getattr(args, "python", sys.executable))
    dance_script = Path(getattr(args, "music_dance_script", default_music_dance_script()))
    if dance_script.exists():
        command = build_music_dance_command(
            python_executable,
            dance_script,
            pid_file=pid_file,
            action="stop",
        )
        try:
            result = runner(command, timeout=float(getattr(args, "music_timeout", 8.0)), check=False)
            return int(getattr(result, "returncode", 0))
        except Exception as exc:
            print("[MUSIC_DANCE] stop failed: {!r}".format(exc), flush=True)
            return 1
    return 0


def run_music_command(
    action: str,
    args: object,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> int:
    if action == "stop":
        stop_music_dance_daemon(args, runner=runner)

    python_executable = Path(getattr(args, "python", sys.executable))
    command = build_music_player_command(
        python_executable,
        Path(getattr(args, "music_player", DEFAULT_MUSIC_PLAYER)),
        music_dir=Path(getattr(args, "music_dir", DEFAULT_MUSIC_DIR)),
        song=getattr(args, "music_song", ""),
        volume=int(getattr(args, "music_volume", 85)),
        loop=bool(getattr(args, "music_loop", False)),
        action=action,
    )
    print("[MUSIC] command:", " ".join(command), flush=True)
    try:
        result = runner(command, timeout=float(getattr(args, "music_timeout", 8.0)), check=False)
    except Exception as exc:
        print("[MUSIC] failed: {!r}".format(exc), flush=True)
        return 1
    return int(getattr(result, "returncode", 1))


def run_music_dance_command(
    action: str,
    args: object,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> int:
    if action == "stop":
        return run_music_command("stop", args, runner=runner)

    python_executable = Path(getattr(args, "python", sys.executable))
    command = build_music_dance_command(
        python_executable,
        Path(getattr(args, "music_dance_script", default_music_dance_script())),
        music_player=Path(getattr(args, "music_player", DEFAULT_MUSIC_PLAYER)),
        music_dir=Path(getattr(args, "music_dir", DEFAULT_MUSIC_DIR)),
        song=getattr(args, "music_song", ""),
        volume=int(getattr(args, "music_volume", 85)),
        loop=bool(getattr(args, "music_loop", False)),
        pid_file=Path(getattr(args, "music_dance_pid", DEFAULT_MUSIC_DANCE_PID)),
        dance_actions=str(getattr(args, "dance_actions", "23,16,15")),
        action_seconds=float(getattr(args, "dance_action_seconds", 3.0)),
        action=action,
    )
    print("[MUSIC_DANCE] command:", " ".join(command), flush=True)
    try:
        result = runner(command, timeout=float(getattr(args, "music_timeout", 8.0)), check=False)
    except Exception as exc:
        print("[MUSIC_DANCE] failed: {!r}".format(exc), flush=True)
        return 1
    return int(getattr(result, "returncode", 1))
