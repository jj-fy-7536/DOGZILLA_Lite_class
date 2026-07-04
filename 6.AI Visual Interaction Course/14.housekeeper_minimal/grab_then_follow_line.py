#!/home/pi/RaspberryPi-CM5/xgovenv/bin/python
"""Minimal housekeeper loop: grab the red ball, then follow the line."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import NamedTuple


ROBOT_PYTHON = Path("/home/pi/RaspberryPi-CM5/xgovenv/bin/python")
GRAB_RESULT_PATH = Path("/home/pi/xgoPictures/ball_grab/grab_result.json")
LINE_RESULT_PATH = Path("/home/pi/xgoPictures/housekeeper/line_result.json")
TASK_QR_MAP_PATH = Path("/home/pi/dogzilla_runs/task_qr_map.json")
DEFAULT_TASK_QR_TIMEOUT_SECONDS = 60.0
DEFAULT_TASK_QR_PROMPT = "请把任务二维码放到摄像头前"
DEFAULT_TASK_QR_PROMPT_DELAY_SECONDS = 2.0

TASK_HOME_TO_DEST = "home_to_dest"
TASK_DEST_TO_HOME = "dest_to_home"
TASK_QR = "qr"
TASK_LEGACY = "legacy"
SUPPORTED_TASK_MODES = (TASK_QR, TASK_LEGACY, TASK_HOME_TO_DEST, TASK_DEST_TO_HOME)
TASK_ALIASES = {
    "task_home_to_dest": TASK_HOME_TO_DEST,
    "home_to_dest": TASK_HOME_TO_DEST,
    "home2dest": TASK_HOME_TO_DEST,
    "起点到目的地": TASK_HOME_TO_DEST,
    "task_dest_to_home": TASK_DEST_TO_HOME,
    "dest_to_home": TASK_DEST_TO_HOME,
    "dest2home": TASK_DEST_TO_HOME,
    "目的地到起点": TASK_DEST_TO_HOME,
}


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
    stop_on_black_block: bool = False,
    stop_block_ignore_seconds: float = 2.0,
    stop_block_required_frames: int = 5,
) -> list[str]:
    command = [str(python_executable), "-u", str(paths.line_script)]
    if target_station:
        command.extend(["--target-station", target_station])
    if target_station or stop_on_black_block:
        command.extend(
            [
                "--line-result",
                str(line_result),
                "--line-mode",
                line_mode,
            ]
        )
    if target_station:
        command.extend(["--qr-decode-every-frames", str(max(1, qr_decode_every_frames))])
    if stop_on_black_block:
        command.extend(
            [
                "--stop-on-black-block",
                "--stop-block-ignore-seconds",
                _format_seconds(stop_block_ignore_seconds),
                "--stop-block-required-frames",
                str(max(1, int(stop_block_required_frames))),
            ]
        )
    return command


def normalize_task_mode(value: str, task_aliases: dict[str, str] | None = None) -> str:
    value = str(value or "").strip()
    if not value:
        return TASK_HOME_TO_DEST
    aliases = dict(TASK_ALIASES)
    if task_aliases:
        aliases.update(task_aliases)
    return aliases.get(value, value)


def task_mode_from_qr_codes(
    codes: list[str] | tuple[str, ...],
    task_aliases: dict[str, str] | None = None,
) -> str | None:
    for code in codes:
        task_mode = normalize_task_mode(code, task_aliases)
        if task_mode in (TASK_HOME_TO_DEST, TASK_DEST_TO_HOME):
            return task_mode
    return None


def load_task_qr_map(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError("invalid task QR map {}: {}".format(path, exc)) from exc
    if not isinstance(data, dict):
        raise ValueError("invalid task QR map {}: root must be an object".format(path))

    task_map: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, str):
            task_mode = normalize_task_mode(value)
            if task_mode not in (TASK_HOME_TO_DEST, TASK_DEST_TO_HOME):
                raise ValueError("invalid task mode for QR code {}: {}".format(key, value))
            task_map[str(key).strip()] = task_mode
        elif isinstance(value, list):
            task_mode = normalize_task_mode(key)
            if task_mode not in (TASK_HOME_TO_DEST, TASK_DEST_TO_HOME):
                raise ValueError("invalid grouped task mode in QR map: {}".format(key))
            for item in value:
                qr_text = str(item).strip()
                if qr_text:
                    task_map[qr_text] = task_mode
        else:
            raise ValueError("invalid QR map value for {}: expected string or list".format(key))
    return task_map


def delivery_workflow_steps(task_mode: str) -> tuple[str, ...]:
    task_mode = normalize_task_mode(task_mode)
    if task_mode == TASK_HOME_TO_DEST:
        return ("grab", "align_outbound", "line_outbound", "release")
    if task_mode == TASK_DEST_TO_HOME:
        return (
            "align_outbound",
            "line_outbound",
            "grab",
            "turn_home",
            "align_return",
            "line_return",
            "release",
        )
    raise ValueError("unsupported delivery task mode: {}".format(task_mode))


def resolve_delivery_task_mode(
    task_mode: str,
    *,
    qr_timeout_seconds: float,
    scanner=None,
    task_aliases: dict[str, str] | None = None,
) -> str | None:
    task_mode = normalize_task_mode(task_mode, task_aliases)
    if task_mode == TASK_QR:
        if scanner is None:
            scanned_task_mode = scan_task_qr(qr_timeout_seconds, task_aliases=task_aliases)
        else:
            scanned_task_mode = scanner(qr_timeout_seconds)
        if scanned_task_mode is None:
            return None
        task_mode = normalize_task_mode(scanned_task_mode, task_aliases)
    if task_mode not in SUPPORTED_TASK_MODES:
        raise ValueError("unsupported delivery task mode: {}".format(task_mode))
    return task_mode


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


def build_release_command() -> list[str]:
    script = (
        "import time\n"
        "from xgolib import XGO\n"
        "dog = XGO(port='/dev/ttyAMA0', version='xgolite')\n"
        "dog.move('x', 0)\n"
        "dog.move('y', 0)\n"
        "dog.turn(0)\n"
        "dog.stop()\n"
        "dog.claw(0)\n"
        "time.sleep(0.8)\n"
        "dog.claw(0)\n"
        "time.sleep(0.4)\n"
        "dog.stop()\n"
    )
    return [sys.executable, "-c", script]


def decode_task_qr_codes(frame_rgb) -> list[str]:
    codes: list[str] = []
    try:
        import cv2
    except ImportError:
        cv2 = None
    try:
        import pyzbar.pyzbar as pyzbar
    except ImportError:
        pyzbar = None

    if pyzbar is not None and cv2 is not None:
        gray = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
        codes.extend(barcode.data.decode("utf-8") for barcode in pyzbar.decode(gray))

    if not codes and cv2 is not None:
        detector = cv2.QRCodeDetector()
        ok, decoded, _points, _straight = detector.detectAndDecodeMulti(frame_rgb)
        if ok:
            codes.extend(code for code in decoded if code)
        if not codes:
            code, _points, _straight = detector.detectAndDecode(frame_rgb)
            if code:
                codes.append(code)
    return codes


def scan_task_qr(
    timeout_seconds: float = 10.0,
    *,
    task_aliases: dict[str, str] | None = None,
) -> str | None:
    from picamera2 import Picamera2

    camera = Picamera2()
    camera.configure(
        camera.create_preview_configuration(main={"format": "RGB888", "size": (320, 240)})
    )
    camera.start()
    deadline = time.time() + max(0.1, float(timeout_seconds))
    try:
        while time.time() < deadline:
            frame = camera.capture_array()
            publish_task_qr_frame(frame)
            codes = decode_task_qr_codes(frame)
            task_mode = task_mode_from_qr_codes(codes, task_aliases)
            if task_mode is not None:
                print("TASK_QR_RECOGNIZED {} FROM {}".format(task_mode, ",".join(codes)), flush=True)
                return task_mode
            if codes:
                print("TASK_QR_IGNORED {}".format(",".join(codes)), flush=True)
            time.sleep(0.1)
        print("TASK_QR_TIMEOUT", flush=True)
        return None
    finally:
        camera.stop()
        camera.close()


def publish_task_qr_frame(frame_rgb, *, cv2_module=None, frame_bus_module=None) -> None:
    try:
        cv2 = cv2_module
        if cv2 is None:
            import cv2
        frame_bus = frame_bus_module
        if frame_bus is None:
            import frame_bus
        frame_bus.publish_bgr(cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR), "task_qr")
    except Exception:
        pass


def announce_task_qr_scan(
    prompt: str,
    delay_seconds: float,
    *,
    printer=print,
    sleeper=time.sleep,
) -> None:
    prompt = str(prompt or "").strip()
    if prompt:
        printer("TASK_QR_SCAN_START {}".format(prompt))
    delay_seconds = max(0.0, float(delay_seconds))
    if delay_seconds > 0:
        sleeper(delay_seconds)


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


_active_child: subprocess.Popen | None = None
_active_child_lock = threading.Lock()
_interrupt_handlers_installed = False


def set_active_child(process: subprocess.Popen | None) -> None:
    global _active_child
    with _active_child_lock:
        _active_child = process


def terminate_child_process(
    process: subprocess.Popen,
    *,
    grace_seconds: float = 3.0,
) -> bool:
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


def terminate_active_child(*, grace_seconds: float = 3.0) -> bool:
    global _active_child
    with _active_child_lock:
        process = _active_child
    if process is None:
        return True
    graceful = terminate_child_process(process, grace_seconds=grace_seconds)
    with _active_child_lock:
        if _active_child is process:
            _active_child = None
    return graceful


def install_workflow_interrupt_handlers() -> None:
    global _interrupt_handlers_installed
    if _interrupt_handlers_installed:
        return

    def handle_interrupt(signum: int, _frame) -> None:
        print("\n[INTERRUPT] stopping workflow children", flush=True)
        terminate_active_child()
        stop_robot_motion()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, handle_interrupt)
    signal.signal(signal.SIGTERM, handle_interrupt)
    _interrupt_handlers_installed = True


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
    set_active_child(process)
    try:
        return process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        print("{} reached timeout; stopping it.".format(name))
        terminate_child_process(process)
        return process.wait()
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt; stopping {}.".format(name))
        terminate_child_process(process)
        raise
    finally:
        set_active_child(None)


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


def _format_seconds(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def run_grab_phase(
    args: argparse.Namespace,
    paths: WorkflowPaths,
    *,
    runner=run_process,
    grab_success_checker=grab_succeeded,
    sleep_fn=time.sleep,
) -> int:
    if args.skip_grab:
        print("Skipping grab by request.")
        return 0

    remove_stale_grab_result(args.grab_result)
    grab_code = runner(
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
        print("Grab process failed with exit code {}.".format(grab_code))
        return grab_code
    if not grab_success_checker(args.grab_result):
        print("Grab did not report success.")
        return 3

    print("Grab succeeded. Releasing camera before next phase.")
    sleep_fn(1.0)
    return 0


def run_align_phase(
    args: argparse.Namespace,
    paths: WorkflowPaths,
    mode: str,
    *,
    runner=run_process,
    sleep_fn=time.sleep,
) -> int:
    if args.skip_align:
        print("Skipping line alignment by request.")
        return 0

    label = "FIND_AND_ALIGN_LINE_{}".format(mode.upper())
    align_code = runner(
        label,
        build_align_command(args.python, paths, args.align_arg),
        timeout_seconds=positive_seconds_or_none(args.align_timeout),
    )
    if align_code != 0:
        print("Line alignment failed with exit code {}.".format(align_code))
        return align_code

    print("Line alignment succeeded. Starting line following.")
    sleep_fn(0.5)
    return 0


def run_line_phase(
    args: argparse.Namespace,
    paths: WorkflowPaths,
    mode: str,
    *,
    timeout_seconds: float,
    runner=run_process,
) -> int:
    if args.skip_line:
        print("Skipping line following by request.")
        return 0

    return runner(
        "FOLLOW_LINE_{}".format(mode.upper()),
        build_line_command(
            args.python,
            paths,
            line_result=args.line_result,
            line_mode=mode,
            qr_decode_every_frames=args.qr_decode_every_frames,
            stop_on_black_block=True,
            stop_block_ignore_seconds=args.stop_block_ignore_seconds,
            stop_block_required_frames=args.stop_block_required_frames,
        ),
        cwd=paths.line_dir,
        timeout_seconds=positive_seconds_or_none(timeout_seconds),
    )


def run_release_phase(
    args: argparse.Namespace,
    *,
    runner=run_process,
) -> int:
    if args.skip_release:
        print("Skipping ball release by request.")
        return 0
    return runner("RELEASE_BALL", build_release_command())


def run_delivery_workflow(
    args: argparse.Namespace,
    paths: WorkflowPaths,
    task_mode: str,
    *,
    runner=run_process,
    grab_success_checker=grab_succeeded,
    sleep_fn=time.sleep,
    stopper=stop_robot_motion,
) -> int:
    print("Delivery task mode: {}".format(task_mode))
    for step in delivery_workflow_steps(task_mode):
        if step == "grab":
            code = run_grab_phase(
                args,
                paths,
                runner=runner,
                grab_success_checker=grab_success_checker,
                sleep_fn=sleep_fn,
            )
        elif step == "align_outbound":
            code = run_align_phase(args, paths, "outbound", runner=runner, sleep_fn=sleep_fn)
        elif step == "line_outbound":
            code = run_line_phase(
                args,
                paths,
                "outbound",
                timeout_seconds=args.line_seconds,
                runner=runner,
            )
        elif step == "turn_home":
            code = runner(
                "TURN_HOME",
                build_turn_home_command(args.turn_home_speed, args.turn_home_seconds),
            )
        elif step == "align_return":
            code = run_align_phase(args, paths, "return", runner=runner, sleep_fn=sleep_fn)
        elif step == "line_return":
            code = run_line_phase(
                args,
                paths,
                "return",
                timeout_seconds=args.return_timeout,
                runner=runner,
            )
        elif step == "release":
            code = run_release_phase(args, runner=runner)
        else:
            raise ValueError("unsupported workflow step: {}".format(step))

        if code != 0:
            stopper()
            return code
    return 0


def run_legacy_workflow(args: argparse.Namespace, paths: WorkflowPaths) -> int:
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
    parser.add_argument("--skip-release", action="store_true", help="调试用：到达后不松爪放球")
    parser.add_argument("--grab-timeout", type=float, default=0, help="抓球最长秒数；0 表示不限制")
    parser.add_argument("--align-timeout", type=float, default=0, help="找线对齐最长秒数；0 表示用脚本默认值")
    parser.add_argument("--line-seconds", type=float, default=0, help="巡线最长秒数；0 表示一直跑到手动停止")
    parser.add_argument(
        "--task-mode",
        default=TASK_QR,
        help="任务模式: qr/home_to_dest/dest_to_home/legacy；默认先扫任务二维码",
    )
    parser.add_argument(
        "--task-qr-timeout",
        type=float,
        default=DEFAULT_TASK_QR_TIMEOUT_SECONDS,
        help="开始前扫描任务二维码的最长秒数",
    )
    parser.add_argument("--task-qr-prompt", default=DEFAULT_TASK_QR_PROMPT)
    parser.add_argument(
        "--task-qr-prompt-delay-seconds",
        type=float,
        default=DEFAULT_TASK_QR_PROMPT_DELAY_SECONDS,
        help="播报扫码提示后等待几秒再打开扫码",
    )
    parser.add_argument(
        "--task-qr-map",
        type=Path,
        default=TASK_QR_MAP_PATH,
        help="二维码文本到任务模式的 JSON 映射文件",
    )
    parser.add_argument("--target-station", default="", help="旧模式使用：巡线目标二维码内容")
    parser.add_argument("--line-result", type=Path, default=LINE_RESULT_PATH)
    parser.add_argument("--qr-decode-every-frames", type=int, default=3)
    parser.add_argument("--stop-block-ignore-seconds", type=float, default=2.0)
    parser.add_argument("--stop-block-required-frames", type=int, default=5)
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

    install_workflow_interrupt_handlers()
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
        task_aliases = load_task_qr_map(args.task_qr_map)
        if normalize_task_mode(args.task_mode, task_aliases) == TASK_QR:
            announce_task_qr_scan(args.task_qr_prompt, args.task_qr_prompt_delay_seconds)
        task_mode = resolve_delivery_task_mode(
            args.task_mode,
            qr_timeout_seconds=args.task_qr_timeout,
            task_aliases=task_aliases,
        )
    except ValueError as exc:
        print(exc)
        return 2
    except Exception as exc:
        print("Task QR scan failed: {}".format(exc))
        return 5

    if task_mode is None:
        print("No known task QR recognized. Expected task_home_to_dest or task_dest_to_home.")
        return 5

    try:
        if task_mode == TASK_LEGACY:
            return run_legacy_workflow(args, paths)
        return run_delivery_workflow(args, paths, task_mode)
    finally:
        stop_robot_motion()


if __name__ == "__main__":
    sys.exit(main())
