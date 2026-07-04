import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


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

    def test_build_turn_home_command_uses_python_one_liner(self):
        command = grab_then_follow_line.build_turn_home_command(
            turn_speed=20,
            turn_seconds=2.4,
        )

        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1], "-c")
        self.assertIn("dog.turn(20)", command[2])
        self.assertIn("time.sleep(2.4)", command[2])

    def test_child_environment_defaults_display_for_opencv_windows(self):
        env = grab_then_follow_line.build_child_env({"PATH": "/bin"})

        self.assertEqual(env["DISPLAY"], ":0")
        self.assertEqual(env["PATH"], "/bin")

        env = grab_then_follow_line.build_child_env({"DISPLAY": ":1"})
        self.assertEqual(env["DISPLAY"], ":1")


if __name__ == "__main__":
    unittest.main()
