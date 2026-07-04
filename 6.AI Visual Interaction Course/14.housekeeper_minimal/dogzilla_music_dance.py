#!/usr/bin/env python3
"""Play music in the background while looping preset dance actions on DOGZILLA Lite."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import music_dance


DEFAULT_DANCE_ACTIONS = (23, 16, 15)
DEFAULT_ACTION_SECONDS = 3.0


def parse_action_ids(text: str) -> tuple[int, ...]:
    actions: list[int] = []
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        actions.append(int(part))
    return tuple(actions) if actions else DEFAULT_DANCE_ACTIONS


def connect_dog():
    from xgolib import XGO

    try:
        return XGO(port="/dev/ttyAMA0", version="xgolite")
    except TypeError:
        return XGO("xgolite")


def safe_call(dog: object, method_name: str, *args: object) -> None:
    method = getattr(dog, method_name, None)
    if not callable(method):
        return
    try:
        method(*args)
    except Exception as exc:
        print("[DANCE] {} failed: {!r}".format(method_name, exc), flush=True)


def reset_dog(dog: object | None) -> None:
    if dog is None:
        return
    for _ in range(3):
        safe_call(dog, "move", "x", 0)
        safe_call(dog, "move", "y", 0)
        safe_call(dog, "turn", 0)
        safe_call(dog, "stop")
        time.sleep(0.04)
    safe_call(dog, "action", 0xFF)
    safe_call(dog, "reset")


def write_pid_file(pid_file: Path) -> None:
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")


def remove_pid_file(pid_file: Path) -> None:
    try:
        pid_file.unlink()
    except FileNotFoundError:
        pass


def sleep_interruptible(seconds: float, stop_event: threading.Event) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline and not stop_event.is_set():
        time.sleep(0.1)


def start_music(args: argparse.Namespace) -> int:
    command = music_dance.build_music_player_command(
        Path(sys.executable),
        args.music_player,
        music_dir=args.music_dir,
        song=args.song,
        volume=args.volume,
        loop=args.loop,
        action="play",
    )
    print("[MUSIC_DANCE] start music:", " ".join(command), flush=True)
    result = subprocess.run(command, timeout=args.music_timeout, check=False)
    return int(result.returncode)


def stop_music(args: argparse.Namespace) -> int:
    command = music_dance.build_music_player_command(
        Path(sys.executable),
        args.music_player,
        music_dir=args.music_dir,
        action="stop",
    )
    print("[MUSIC_DANCE] stop music:", " ".join(command), flush=True)
    result = subprocess.run(command, timeout=args.music_timeout, check=False)
    return int(result.returncode)


def spawn_background(args: argparse.Namespace) -> int:
    command = [
        str(Path(sys.executable)),
        str(Path(__file__).resolve()),
        "--music-player",
        str(args.music_player),
        "--music-dir",
        str(args.music_dir),
        "--volume",
        str(args.volume),
        "--pid-file",
        str(args.pid_file),
        "--dance-actions",
        args.dance_actions,
        "--action-seconds",
        str(args.action_seconds),
        "--music-timeout",
        str(args.music_timeout),
    ]
    if args.song:
        command.extend(["--song", args.song])
    if args.loop:
        command.append("--loop")
    if args.dry_run:
        command.append("--dry-run")
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return 0


def stop_daemon(args: argparse.Namespace) -> int:
    pid = music_dance.read_pid_file(args.pid_file)
    if pid is not None:
        music_dance.terminate_pid(pid)
    remove_pid_file(args.pid_file)
    stop_music(args)
    return 0


def run_dance_loop(args: argparse.Namespace) -> int:
    stop_event = threading.Event()

    def handle_signal(signum: int, _frame) -> None:
        print("[MUSIC_DANCE] received signal {}".format(signum), flush=True)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    write_pid_file(args.pid_file)
    dog = None
    music_code = 0
    try:
        music_code = start_music(args)
        if music_code != 0:
            print("[MUSIC_DANCE] music start failed with code {}".format(music_code), flush=True)
            return music_code

        actions = parse_action_ids(args.dance_actions)
        if args.dry_run:
            index = 0
            while not stop_event.is_set():
                action_id = actions[index % len(actions)]
                print("[DRY] dance action({})".format(action_id), flush=True)
                sleep_interruptible(args.action_seconds, stop_event)
                index += 1
            return 0

        dog = connect_dog()
        safe_call(dog, "reset")
        time.sleep(0.5)
        index = 0
        while not stop_event.is_set():
            action_id = actions[index % len(actions)]
            print("[DANCE] action({})".format(action_id), flush=True)
            safe_call(dog, "action", action_id)
            sleep_interruptible(args.action_seconds, stop_event)
            index += 1
        return 0
    finally:
        stop_music(args)
        reset_dog(dog)
        remove_pid_file(args.pid_file)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Play music while looping preset dance actions")
    parser.add_argument("--background", action="store_true", help="spawn detached dance process and exit")
    parser.add_argument("--stop", action="store_true", help="stop running music+dance daemon")
    parser.add_argument("--dry-run", action="store_true", help="print dance actions without controlling robot")
    parser.add_argument("--music-player", type=Path, default=music_dance.DEFAULT_MUSIC_PLAYER)
    parser.add_argument("--music-dir", type=Path, default=music_dance.DEFAULT_MUSIC_DIR)
    parser.add_argument("--song", default="")
    parser.add_argument("--volume", type=int, default=85)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--music-timeout", type=float, default=8.0)
    parser.add_argument("--pid-file", type=Path, default=music_dance.DEFAULT_MUSIC_DANCE_PID)
    parser.add_argument(
        "--dance-actions",
        default=",".join(str(action) for action in DEFAULT_DANCE_ACTIONS),
        help="comma-separated XGO action ids; default 23,16,15 = Dance/Swing/Wave_Body",
    )
    parser.add_argument("--action-seconds", type=float, default=DEFAULT_ACTION_SECONDS)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.stop:
        return stop_daemon(args)
    if args.background:
        return spawn_background(args)
    return run_dance_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())
