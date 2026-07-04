import argparse
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent))

import music_dance


class MusicDanceModuleTests(unittest.TestCase):
    def test_parse_music_command_prefers_stop_then_dance_then_play(self):
        self.assertEqual(music_dance.parse_music_command("停歌"), "stop")
        self.assertEqual(music_dance.parse_music_command("放歌跳舞"), "dance")
        self.assertEqual(music_dance.parse_music_command("边听边跳"), "dance")
        self.assertEqual(music_dance.parse_music_command("放歌"), "play")
        self.assertIsNone(music_dance.parse_music_command("开始任务"))

    def test_build_music_dance_command_spawns_background_worker(self):
        command = music_dance.build_music_dance_command(
            Path("/python"),
            Path("/home/pi/dogzilla_runs/dogzilla_music_dance.py"),
            music_player=Path("/home/pi/dogzilla_runs/dogzilla_music_player.py"),
            music_dir=Path("/home/pi/dogzilla_runs/music"),
            song="a.mp3",
            volume=70,
            dance_actions="23,16",
            action_seconds=2.5,
            action="play",
        )
        self.assertEqual(command[:2], ["/python", "/home/pi/dogzilla_runs/dogzilla_music_dance.py"])
        self.assertIn("--background", command)
        self.assertIn("--dance-actions", command)
        self.assertIn("23,16", command)

    def test_run_music_command_stop_also_stops_dance_daemon(self):
        args = argparse.Namespace(
            python=Path("/python"),
            music_player=Path("/player.py"),
            music_dir=Path("/music"),
            music_song="",
            music_volume=80,
            music_loop=False,
            music_timeout=5.0,
            music_dance_script=Path("/dance.py"),
            music_dance_pid=Path("/tmp/music_dance.pid"),
        )
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return mock.Mock(returncode=0)

        with mock.patch.object(music_dance, "stop_music_dance_daemon", return_value=0) as stop_dance:
            code = music_dance.run_music_command("stop", args, runner=runner)
        self.assertEqual(code, 0)
        stop_dance.assert_called_once()


class DogzillaMusicDanceScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module_path = Path(__file__).with_name("dogzilla_music_dance.py")
        spec = importlib.util.spec_from_file_location("dogzilla_music_dance", module_path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_spawn_background_starts_detached_process(self):
        args = self.module.build_parser().parse_args(
            [
                "--background",
                "--music-player",
                "/player.py",
                "--music-dir",
                "/music",
            ]
        )
        with mock.patch.object(self.module.subprocess, "Popen") as popen:
            code = self.module.spawn_background(args)
        self.assertEqual(code, 0)
        popen.assert_called_once()
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_dry_run_dance_loop_stops_after_interruptible_sleep(self):
        args = self.module.build_parser().parse_args(
            [
                "--dry-run",
                "--music-player",
                "/player.py",
                "--music-dir",
                "/music",
                "--action-seconds",
                "0.05",
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            args.pid_file = Path(temp_dir) / "music_dance.pid"

            def stop_after_first(_seconds, stop_event):
                stop_event.set()

            with mock.patch.object(self.module, "start_music", return_value=0), mock.patch.object(
                self.module,
                "stop_music",
                return_value=0,
            ), mock.patch.object(self.module, "sleep_interruptible", side_effect=stop_after_first):
                code = self.module.run_dance_loop(args)

            self.assertEqual(code, 0)
            self.assertFalse(args.pid_file.exists())


if __name__ == "__main__":
    unittest.main()
