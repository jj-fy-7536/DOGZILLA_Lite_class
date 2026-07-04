#!/home/pi/RaspberryPi-CM5/xgovenv/bin/python
"""Minimal housekeeper loop: grab the red ball, then follow the line."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple


ROBOT_PYTHON = Path("/home/pi/RaspberryPi-CM5/xgovenv/bin/python")
GRAB_RESULT_PATH = Path("/home/pi/xgoPictures/ball_grab/grab_result.json")
LINE_RESULT_PATH = Path("/home/pi/xgoPictures/housekeeper/line_result.json")


class WorkflowPaths(NamedTuple):
    grab_script: Path
    align_script: Path
    line_script: Path
    line_dir: Path


def default_course_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_paths(course_dir: Path | None = None) -> WorkflowPaths:
    course_dir = Path(course_dir) if course_dir is not None else default_course_dir()
    grab_script = course_dir / "11.pick it up" / "ball_grab_v3.py"
    align_script = course_dir / "14.housekeeper_minimal" / "find_and_align_line.py"
    line_dir = course_dir / "01.color_line"
    line_script = line_dir / "follow_line.py"
    return WorkflowPaths(
        grab_script=grab_script,
        align_script=align_script,
        line_script=line_script,
        line_dir=line_dir,
    )


def build_grab_command(
    python_executable: Path,
    paths: WorkflowPaths,
    stream_grab: bool = False,
    target_color: str = "red",
    extra_args: list[str] | None = None,
) -> list[str]:
    command = [
        str(python_executable),
        str(paths.grab_script),
        "--mode",
        "full",
        "--target-color",
        target_color,
    ]
    if not stream_grab:
        command.append("--no-stream")
    if extra_args:
        command.extend(extra_args)
    return command


def build_line_command(
    python_executable: Path,
    paths: WorkflowPaths,
    *,
    target_station: str = "",
    line_result: Path = LINE_RESULT_PATH,
    line_mode: str = "outbound",
    qr_decode_every_frames: int = 3,
) -> list[str]:
    command = [str(python_executable), "-u", str(paths.line_script)]
    if target_station:
        command.extend(
            [
                "--target-station",
                target_station,
                "--line-result",
                str(line_result),
                "--line-mode",
                line_mode,
                "--qr-decode-every-frames",
                str(max(1, qr_decode_every_frames)),
            ]
        )
    return command


def build_turn_home_command(turn_speed: int, turn_seconds: float) -> list[str]:
    script = (
        "import time\n"
        "from xgolib import XGO\n"
        "dog = XGO(port='/dev/ttyAMA0', version='xgolite')\n"
        "dog.move('x', 0)\n"
        "dog.move('y', 0)\n"
        "dog.turn({})\n"
        "time.sleep({})\n"
        "dog.turn(0)\n"
        "dog.stop()\n"
    ).format(int(turn_speed), float(turn_seconds))
    return [sys.executable, "-c", script]


def build_align_command(
    python_executable: Path,
    paths: WorkflowPaths,
    extra_args: list[str] | None = None,
) -> list[str]:
    command = [str(python_executable), str(paths.align_script)]
    if extra_args:
        command.extend(extra_args)
    return command


def remove_stale_grab_result(result_path: Path) -> None:
    try:
        result_path.unlink()
    except FileNotFoundError:
        pass


def grab_succeeded(result_path: Path) -> bool:
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    return bool(data.get("success"))


def build_child_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env.setdefault("DISPLAY", ":0")
    return env


def run_process(
    name: str,
    command: list[str],
    cwd: Path | None = None,
    timeout_seconds: float | None = None,
) -> int:
    print("\n=== {} ===".format(name))
    print("COMMAND: {}".format(" ".join(command)))
    process = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd is not None else None,
        env=build_child_env(),
    )
    try:
        return process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        print("{} reached timeout; stopping it.".format(name))
        process.send_signal(signal.SIGINT)
        try:
            return process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.wait()
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt; stopping {}.".format(name))
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise


def stop_robot_motion() -> None:
    try:
        from xgolib import XGO

        dog = XGO(port="/dev/ttyAMA0", version="xgolite")
        dog.move("x", 0)
        dog.move("y", 0)
        dog.turn(0)
        dog.stop()
    except Exception as exc:
        print("Robot stop fallback failed: {}".format(exc))


def reset_robot_pose() -> None:
    try:
        from xgolib import XGO

        dog = XGO(port="/dev/ttyAMA0", version="xgolite")
        dog.move("x", 0)
        dog.move("y", 0)
        dog.turn(0)
        dog.stop()
        dog.reset()
    except Exception as exc:
        print("Robot reset fallback failed: {}".format(exc))


def positive_seconds_or_none(value: float) -> float | None:
    return value if value > 0 else None


def main() -> int:
    parser = argparse.ArgumentParser(description="抓球成功后启动巡线的最小闭环")
    parser.add_argument("--course-dir", type=Path, default=default_course_dir())
    parser.add_argument("--python", type=Path, default=ROBOT_PYTHON)
    parser.add_argument("--grab-result", type=Path, default=GRAB_RESULT_PATH)
    parser.add_argument("--target-color", default="red", help="抓取目标颜色: red/green/blue/yellow")
    parser.add_argument("--stream-grab", action="store_true", help="保留抓球实时画面；闭环默认关闭")
    parser.add_argument("--skip-grab", action="store_true", help="调试用：跳过抓球，直接巡线")
    parser.add_argument("--skip-align", action="store_true", help="调试用：跳过抓球后的找线对齐")
    parser.add_argument("--skip-line", action="store_true", help="调试用：只抓球，不巡线")
    parser.add_argument("--grab-timeout", type=float, default=0, help="抓球最长秒数；0 表示不限制")
    parser.add_argument("--align-timeout", type=float, default=0, help="找线对齐最长秒数；0 表示用脚本默认值")
    parser.add_argument("--line-seconds", type=float, default=0, help="巡线最长秒数；0 表示一直跑到手动停止")
    parser.add_argument("--target-station", default="", help="巡线目标二维码内容；空表示保持旧巡线行为")
    parser.add_argument("--line-result", type=Path, default=LINE_RESULT_PATH)
    parser.add_argument("--qr-decode-every-frames", type=int, default=3)
    parser.add_argument("--return-home", action="store_true", help="到达目标站点后掉头返航到 home")
    parser.add_argument("--home-station", default="home")
    parser.add_argument("--return-timeout", type=float, default=90.0)
    parser.add_argument("--turn-home-speed", type=int, default=20)
    parser.add_argument("--turn-home-seconds", type=float, default=2.4)
    parser.add_argument(
        "--grab-arg",
        action="append",
        default=[],
        help="透传给 ball_grab_v3.py 的额外参数，可重复使用",
    )
    parser.add_argument(
        "--align-arg",
        action="append",
        default=[],
        help="透传给 find_and_align_line.py 的额外参数，可重复使用",
    )
    args = parser.parse_args()

    paths = resolve_paths(args.course_dir)
    for label, path in (
        ("grab script", paths.grab_script),
        ("align script", paths.align_script),
        ("line script", paths.line_script),
    ):
        if not path.exists():
            print("Missing {}: {}".format(label, path))
            return 2

    try:
        if not args.skip_grab:
            remove_stale_grab_result(args.grab_result)
            grab_code = run_process(
                "GRAB",
                build_grab_command(
                    args.python,
                    paths,
                    args.stream_grab,
                    target_color=args.target_color,
                    extra_args=args.grab_arg,
                ),
                timeout_seconds=positive_seconds_or_none(args.grab_timeout),
            )
            if grab_code != 0:
                print("Grab process failed with exit code {}. Line following will not start.".format(grab_code))
                stop_robot_motion()
                return grab_code
            if not grab_succeeded(args.grab_result):
                print("Grab did not report success. Line following will not start.")
                stop_robot_motion()
                return 3

            print("Grab succeeded. Releasing camera before line following.")
            time.sleep(1.0)
        else:
            print("Skipping grab by request.")

        if not args.skip_align:
            align_code = run_process(
                "FIND_AND_ALIGN_LINE",
                build_align_command(args.python, paths, args.align_arg),
                timeout_seconds=positive_seconds_or_none(args.align_timeout),
            )
            if align_code != 0:
                print("Line alignment failed with exit code {}. Line following will not start.".format(align_code))
                stop_robot_motion()
                return align_code

            print("Line alignment succeeded. Starting line following.")
            time.sleep(0.5)
        else:
            print("Skipping line alignment by request.")

        if args.skip_line:
            print("Skipping line following by request.")
            return 0

        line_code = run_process(
            "FOLLOW_LINE",
            build_line_command(
                args.python,
                paths,
                target_station=args.target_station,
                line_result=args.line_result,
                line_mode="outbound",
                qr_decode_every_frames=args.qr_decode_every_frames,
            ),
            cwd=paths.line_dir,
            timeout_seconds=positive_seconds_or_none(args.line_seconds),
        )
        if line_code != 0:
            return line_code
        if not args.return_home:
            return line_code

        if not args.target_station:
            print("Return-home requested but outbound target station is empty.")
            return 4

        turn_code = run_process(
            "TURN_HOME",
            build_turn_home_command(args.turn_home_speed, args.turn_home_seconds),
        )
        if turn_code != 0:
            stop_robot_motion()
            return turn_code

        return_code = run_process(
            "RETURN_HOME",
            build_line_command(
                args.python,
                paths,
                target_station=args.home_station,
                line_result=args.line_result,
                line_mode="return",
                qr_decode_every_frames=args.qr_decode_every_frames,
            ),
            cwd=paths.line_dir,
            timeout_seconds=positive_seconds_or_none(args.return_timeout),
        )
        if return_code == 0:
            reset_robot_pose()
        return return_code
    finally:
        stop_robot_motion()


if __name__ == "__main__":
    sys.exit(main())
