import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).with_name("grab_then_follow_line.py")
SPEC = importlib.util.spec_from_file_location("grab_then_follow_line", MODULE_PATH)
grab_then_follow_line = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(grab_then_follow_line)


class GrabThenFollowLineTests(unittest.TestCase):
    def test_resolve_paths_points_to_course_grab_and_line_modules(self):
        course_dir = Path(__file__).resolve().parents[1]

        paths = grab_then_follow_line.resolve_paths(course_dir)

        self.assertEqual(paths.grab_script.name, "ball_grab_v3.py")
        self.assertEqual(paths.grab_script.parent.name, "11.pick it up")
        self.assertTrue(paths.grab_script.exists())
        self.assertEqual(paths.align_script.name, "find_and_align_line.py")
        self.assertEqual(paths.align_script.parent.name, "14.housekeeper_minimal")
        self.assertTrue(paths.align_script.exists())
        self.assertEqual(paths.line_script.name, "follow_line.py")
        self.assertEqual(paths.line_script.parent.name, "01.color_line")
        self.assertTrue(paths.line_script.exists())

    def test_grab_command_disables_stream_by_default_so_line_can_start(self):
        paths = grab_then_follow_line.WorkflowPaths(
            grab_script=Path("/course/11.pick it up/ball_grab_v3.py"),
            align_script=Path("/course/14.housekeeper_minimal/find_and_align_line.py"),
            line_script=Path("/course/01.color_line/follow_line.py"),
            line_dir=Path("/course/01.color_line"),
        )

        command = grab_then_follow_line.build_grab_command(
            Path("/home/pi/RaspberryPi-CM5/xgovenv/bin/python"),
            paths,
            stream_grab=False,
            target_color="green",
        )

        self.assertEqual(command[:3], [
            "/home/pi/RaspberryPi-CM5/xgovenv/bin/python",
            "/course/11.pick it up/ball_grab_v3.py",
            "--mode",
        ])
        self.assertIn("--no-stream", command)
        self.assertIn("--target-color", command)
        self.assertIn("green", command)

    def test_align_command_runs_before_line_following(self):
        paths = grab_then_follow_line.WorkflowPaths(
            grab_script=Path("/course/11.pick it up/ball_grab_v3.py"),
            align_script=Path("/course/14.housekeeper_minimal/find_and_align_line.py"),
            line_script=Path("/course/01.color_line/follow_line.py"),
            line_dir=Path("/course/01.color_line"),
        )

        command = grab_then_follow_line.build_align_command(
            Path("/home/pi/RaspberryPi-CM5/xgovenv/bin/python"),
            paths,
            extra_args=["--scan-seconds", "5"],
        )

        self.assertEqual(command[:2], [
            "/home/pi/RaspberryPi-CM5/xgovenv/bin/python",
            "/course/14.housekeeper_minimal/find_and_align_line.py",
        ])
        self.assertEqual(command[-2:], ["--scan-seconds", "5"])

    def test_grab_result_controls_whether_line_following_starts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result_path = Path(temp_dir) / "grab_result.json"

            result_path.write_text(json.dumps({"success": True}), encoding="utf-8")
            self.assertTrue(grab_then_follow_line.grab_succeeded(result_path))

            result_path.write_text(json.dumps({"success": False}), encoding="utf-8")
            self.assertFalse(grab_then_follow_line.grab_succeeded(result_path))

            result_path.unlink()
            self.assertFalse(grab_then_follow_line.grab_succeeded(result_path))

    def test_line_command_targets_station_and_writes_result(self):
        paths = grab_then_follow_line.WorkflowPaths(
            grab_script=Path("/course/11.pick it up/ball_grab_v3.py"),
            align_script=Path("/course/14.housekeeper_minimal/find_and_align_line.py"),
            line_script=Path("/course/01.color_line/follow_line.py"),
            line_dir=Path("/course/01.color_line"),
        )

        command = grab_then_follow_line.build_line_command(
            Path("/python"),
            paths,
            target_station="station_B",
            line_result=Path("/tmp/line_result.json"),
            line_mode="outbound",
            qr_decode_every_frames=3,
            stop_on_black_block=True,
            stop_block_ignore_seconds=2.5,
            stop_block_required_frames=4,
        )

        self.assertEqual(command[:3], ["/python", "-u", "/course/01.color_line/follow_line.py"])
        self.assertIn("--target-station", command)
        self.assertIn("station_B", command)
        self.assertIn("--line-result", command)
        self.assertIn("/tmp/line_result.json", command)
        self.assertIn("--line-mode", command)
        self.assertIn("outbound", command)
        self.assertIn("--qr-decode-every-frames", command)
        self.assertIn("3", command)
        self.assertIn("--stop-on-black-block", command)
        self.assertIn("--stop-block-ignore-seconds", command)
        self.assertIn("2.5", command)
        self.assertIn("--stop-block-required-frames", command)
        self.assertIn("4", command)

    def test_line_command_can_stop_on_black_block_without_qr_station(self):
        paths = grab_then_follow_line.WorkflowPaths(
            grab_script=Path("/course/11.pick it up/ball_grab_v3.py"),
            align_script=Path("/course/14.housekeeper_minimal/find_and_align_line.py"),
            line_script=Path("/course/01.color_line/follow_line.py"),
            line_dir=Path("/course/01.color_line"),
        )

        command = grab_then_follow_line.build_line_command(
            Path("/python"),
            paths,
            line_result=Path("/tmp/line_result.json"),
            line_mode="outbound",
            stop_on_black_block=True,
        )

        self.assertIn("--line-result", command)
        self.assertIn("/tmp/line_result.json", command)
        self.assertIn("--line-mode", command)
        self.assertIn("outbound", command)
        self.assertIn("--stop-on-black-block", command)
        self.assertNotIn("--target-station", command)

    def test_build_turn_home_command_uses_python_one_liner(self):
        command = grab_then_follow_line.build_turn_home_command(
            turn_speed=20,
            turn_seconds=2.4,
        )

        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1], "-c")
        self.assertIn("dog.turn(20)", command[2])
        self.assertIn("time.sleep(2.4)", command[2])

    def test_release_command_opens_claw(self):
        command = grab_then_follow_line.build_release_command()

        self.assertEqual(command[0], sys.executable)
        self.assertIn("dog.claw(0)", command[2])

    def test_delivery_workflow_steps_for_two_qr_tasks(self):
        self.assertEqual(
            grab_then_follow_line.delivery_workflow_steps("task_home_to_dest"),
            ("grab", "align_outbound", "line_outbound", "release"),
        )
        self.assertEqual(
            grab_then_follow_line.delivery_workflow_steps("task_dest_to_home"),
            (
                "align_outbound",
                "line_outbound",
                "grab",
                "turn_home",
                "align_return",
                "line_return",
                "release",
            ),
        )

    def test_task_mode_from_qr_codes_accepts_known_task_codes(self):
        self.assertEqual(
            grab_then_follow_line.task_mode_from_qr_codes(["noise", "task_home_to_dest"]),
            "home_to_dest",
        )
        self.assertEqual(
            grab_then_follow_line.task_mode_from_qr_codes(["dest2home"]),
            "dest_to_home",
        )
        self.assertIsNone(grab_then_follow_line.task_mode_from_qr_codes(["station_A"]))

    def test_task_mode_from_qr_codes_accepts_custom_qr_map(self):
        qr_map = {
            "送去目的地": "home_to_dest",
            "拿回起点": "dest_to_home",
        }

        self.assertEqual(
            grab_then_follow_line.task_mode_from_qr_codes(["送去目的地"], qr_map),
            "home_to_dest",
        )
        self.assertEqual(
            grab_then_follow_line.task_mode_from_qr_codes(["拿回起点"], qr_map),
            "dest_to_home",
        )

    def test_load_task_qr_map_accepts_direct_and_grouped_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            map_path = Path(temp_dir) / "task_qr_map.json"
            map_path.write_text(
                json.dumps(
                    {
                        "送去目的地": "home_to_dest",
                        "dest_to_home": ["拿回起点", "取回起点"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            qr_map = grab_then_follow_line.load_task_qr_map(map_path)

        self.assertEqual(qr_map["送去目的地"], "home_to_dest")
        self.assertEqual(qr_map["拿回起点"], "dest_to_home")
        self.assertEqual(qr_map["取回起点"], "dest_to_home")

    def test_task_qr_scan_announcement_prints_prompt_and_waits_before_scanning(self):
        messages = []
        delays = []

        grab_then_follow_line.announce_task_qr_scan(
            "请把任务二维码放到摄像头前",
            2.5,
            printer=messages.append,
            sleeper=delays.append,
        )

        self.assertEqual(messages, ["TASK_QR_SCAN_START 请把任务二维码放到摄像头前"])
        self.assertEqual(delays, [2.5])

    def test_default_task_qr_timeout_is_long_enough_for_manual_scan(self):
        self.assertGreaterEqual(grab_then_follow_line.DEFAULT_TASK_QR_TIMEOUT_SECONDS, 60.0)

    def test_publish_task_qr_frame_uses_frame_bus_when_available(self):
        calls = []

        class FakeCv2:
            COLOR_RGB2BGR = 1

            @staticmethod
            def cvtColor(frame, color):
                return ("bgr", frame, color)

        class FakeFrameBus:
            @staticmethod
            def publish_bgr(frame, source):
                calls.append((frame, source))

        grab_then_follow_line.publish_task_qr_frame(
            "rgb-frame",
            cv2_module=FakeCv2,
            frame_bus_module=FakeFrameBus,
        )

        self.assertEqual(calls, [(("bgr", "rgb-frame", 1), "task_qr")])

    def test_resolve_delivery_task_mode_scans_qr_when_requested(self):
        scanned = []

        def scanner(timeout_seconds):
            scanned.append(timeout_seconds)
            return "dest_to_home"

        task_mode = grab_then_follow_line.resolve_delivery_task_mode(
            "qr",
            qr_timeout_seconds=6.5,
            scanner=scanner,
        )

        self.assertEqual(task_mode, "dest_to_home")
        self.assertEqual(scanned, [6.5])

    def test_resolve_delivery_task_mode_accepts_explicit_task_alias(self):
        task_mode = grab_then_follow_line.resolve_delivery_task_mode(
            "task_home_to_dest",
            qr_timeout_seconds=6.5,
            scanner=lambda _: "dest_to_home",
        )

        self.assertEqual(task_mode, "home_to_dest")

    def test_delivery_workflow_home_to_dest_grabs_then_stops_on_black_block_and_releases(self):
        paths = grab_then_follow_line.WorkflowPaths(
            grab_script=Path("/course/11.pick it up/ball_grab_v3.py"),
            align_script=Path("/course/14.housekeeper_minimal/find_and_align_line.py"),
            line_script=Path("/course/01.color_line/follow_line.py"),
            line_dir=Path("/course/01.color_line"),
        )
        args = SimpleNamespace(
            python=Path("/python"),
            stream_grab=False,
            target_color="red",
            grab_arg=[],
            grab_result=Path("/tmp/grab_result.json"),
            grab_timeout=12.0,
            skip_grab=False,
            align_arg=[],
            align_timeout=5.0,
            skip_align=False,
            line_seconds=30.0,
            skip_line=False,
            line_result=Path("/tmp/line_result.json"),
            qr_decode_every_frames=3,
            stop_block_ignore_seconds=2.5,
            stop_block_required_frames=4,
            turn_home_speed=20,
            turn_home_seconds=2.4,
            return_timeout=90.0,
            skip_release=False,
        )
        calls = []

        def runner(name, command, cwd=None, timeout_seconds=None):
            calls.append((name, command, cwd, timeout_seconds))
            return 0

        code = grab_then_follow_line.run_delivery_workflow(
            args,
            paths,
            "home_to_dest",
            runner=runner,
            grab_success_checker=lambda _: True,
            sleep_fn=lambda _: None,
            stopper=lambda: None,
        )

        self.assertEqual(code, 0)
        self.assertEqual([call[0] for call in calls], [
            "GRAB",
            "FIND_AND_ALIGN_LINE_OUTBOUND",
            "FOLLOW_LINE_OUTBOUND",
            "RELEASE_BALL",
        ])
        line_command = calls[2][1]
        self.assertIn("--stop-on-black-block", line_command)
        self.assertIn("--stop-block-ignore-seconds", line_command)
        self.assertIn("2.5", line_command)
        self.assertIn("--stop-block-required-frames", line_command)
        self.assertIn("4", line_command)
        self.assertNotIn("--target-station", line_command)
        self.assertEqual(calls[2][2], Path("/course/01.color_line"))

    def test_delivery_workflow_dest_to_home_lines_out_grabs_returns_and_releases(self):
        paths = grab_then_follow_line.WorkflowPaths(
            grab_script=Path("/course/11.pick it up/ball_grab_v3.py"),
            align_script=Path("/course/14.housekeeper_minimal/find_and_align_line.py"),
            line_script=Path("/course/01.color_line/follow_line.py"),
            line_dir=Path("/course/01.color_line"),
        )
        args = SimpleNamespace(
            python=Path("/python"),
            stream_grab=False,
            target_color="red",
            grab_arg=[],
            grab_result=Path("/tmp/grab_result.json"),
            grab_timeout=12.0,
            skip_grab=False,
            align_arg=[],
            align_timeout=5.0,
            skip_align=False,
            line_seconds=30.0,
            skip_line=False,
            line_result=Path("/tmp/line_result.json"),
            qr_decode_every_frames=3,
            stop_block_ignore_seconds=2.0,
            stop_block_required_frames=5,
            turn_home_speed=22,
            turn_home_seconds=2.8,
            return_timeout=80.0,
            skip_release=False,
        )
        calls = []

        def runner(name, command, cwd=None, timeout_seconds=None):
            calls.append((name, command, cwd, timeout_seconds))
            return 0

        code = grab_then_follow_line.run_delivery_workflow(
            args,
            paths,
            "dest_to_home",
            runner=runner,
            grab_success_checker=lambda _: True,
            sleep_fn=lambda _: None,
            stopper=lambda: None,
        )

        self.assertEqual(code, 0)
        self.assertEqual([call[0] for call in calls], [
            "FIND_AND_ALIGN_LINE_OUTBOUND",
            "FOLLOW_LINE_OUTBOUND",
            "GRAB",
            "TURN_HOME",
            "FIND_AND_ALIGN_LINE_RETURN",
            "FOLLOW_LINE_RETURN",
            "RELEASE_BALL",
        ])
        self.assertIn("--stop-on-black-block", calls[1][1])
        self.assertIn("--stop-on-black-block", calls[5][1])
        self.assertEqual(calls[1][3], 30.0)
        self.assertEqual(calls[5][3], 80.0)

    def test_child_environment_defaults_display_for_opencv_windows(self):
        env = grab_then_follow_line.build_child_env({"PATH": "/bin"})

        self.assertEqual(env["DISPLAY"], ":0")
        self.assertEqual(env["PATH"], "/bin")

        env = grab_then_follow_line.build_child_env({"DISPLAY": ":1"})
        self.assertEqual(env["DISPLAY"], ":1")


if __name__ == "__main__":
    unittest.main()
