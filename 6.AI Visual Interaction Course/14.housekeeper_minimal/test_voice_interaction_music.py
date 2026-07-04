import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

MODULE_PATH = Path(__file__).with_name("voice_interaction.py")
SPEC = importlib.util.spec_from_file_location("voice_interaction", MODULE_PATH)
voice_interaction = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = voice_interaction
SPEC.loader.exec_module(voice_interaction)


class VoiceInteractionMusicTests(unittest.TestCase):
    def test_parse_command_accepts_music_play_and_stop(self):
        play = voice_interaction.parse_command("放歌")
        self.assertEqual(play.name, "music_play")

        play_music = voice_interaction.parse_command("播放音乐")
        self.assertEqual(play_music.name, "music_play")

        stop_music_event = voice_interaction.parse_command("停歌")
        self.assertEqual(stop_music_event.name, "music_stop")

        dance = voice_interaction.parse_command("放歌跳舞")
        self.assertEqual(dance.name, "music_dance")

        stop_robot = voice_interaction.parse_command("停止")
        self.assertEqual(stop_robot.name, "stop")

    def test_music_player_command_uses_remote_script_and_background_mode(self):
        command = voice_interaction.build_music_player_command(
            Path("/python"),
            Path("/home/pi/dogzilla_runs/dogzilla_music_player.py"),
            music_dir=Path("/home/pi/dogzilla_runs/music"),
            song="周杰伦",
            volume=66,
            loop=True,
            action="play",
        )

        self.assertEqual(command[:2], ["/python", "/home/pi/dogzilla_runs/dogzilla_music_player.py"])
        self.assertIn("--music-dir", command)
        self.assertIn("/home/pi/dogzilla_runs/music", command)
        self.assertIn("--song", command)
        self.assertIn("周杰伦", command)
        self.assertIn("--volume", command)
        self.assertIn("66", command)
        self.assertIn("--background", command)
        self.assertIn("--loop", command)

    def test_music_player_command_chooses_first_song_when_song_is_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            music_dir = Path(temp_dir)
            (music_dir / "b.mp3").write_bytes(b"")
            (music_dir / "a.mp3").write_bytes(b"")

            command = voice_interaction.build_music_player_command(
                Path("/python"),
                Path("/home/pi/dogzilla_runs/dogzilla_music_player.py"),
                music_dir=music_dir,
                song="",
                action="play",
            )

        self.assertIn("--song", command)
        self.assertIn("a.mp3", command)

    def test_dry_run_music_command_does_not_start_player(self):
        robot = voice_interaction.DogzillaVoiceRobot(dry_run=True)
        command = voice_interaction.Command("music_play")
        args = voice_interaction.build_parser().parse_args(["--dry-run"])

        self.assertTrue(voice_interaction.execute_command(robot, command, args))


if __name__ == "__main__":
    unittest.main()
