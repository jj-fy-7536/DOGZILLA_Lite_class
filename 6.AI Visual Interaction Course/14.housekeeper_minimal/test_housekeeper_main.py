import importlib.util
import json
import argparse
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

MODULE_PATH = Path(__file__).with_name("housekeeper_main.py")
SPEC = importlib.util.spec_from_file_location("housekeeper_main", MODULE_PATH)
housekeeper_main = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = housekeeper_main
SPEC.loader.exec_module(housekeeper_main)

from echo_guard import EchoGuard


class HousekeeperMainTest(unittest.TestCase):
    class FakeSpeaker:
        def __init__(self):
            self.messages = []

        def speak(self, text):
            self.messages.append(text)

    class FakeOnlineTts:
        def __init__(self):
            self.messages = []

        def speak(self, text):
            self.messages.append(text)

    class FakeSparkModule:
        def __init__(self, *, should_chat=True, answer="北京是中国的首都。", error=None):
            self.should_chat = should_chat
            self.answer = answer
            self.error = error
            self.questions = []

        def should_use_spark_chat(self, text, args):
            return self.should_chat and bool(args.spark_api_password)

        def fetch_spark_answer(self, question, args):
            self.questions.append(question)
            if self.error is not None:
                raise self.error
            return self.answer

    def spark_args(self, password="secret"):
        return argparse.Namespace(spark_api_password=password)

    def test_task_trigger_accepts_housekeeper_requests(self):
        for text in ("开始任务", "去捡球", "帮我拿红球", "开始抓球"):
            with self.subTest(text=text):
                task = housekeeper_main.parse_task_trigger(text)
                self.assertIsNotNone(task)
                self.assertEqual(task.name, "grab_then_follow_line")
                self.assertEqual(task.effective_color, "red")
                self.assertEqual(task.target_station, "station_A")

    def test_task_trigger_extracts_color_and_station_slots(self):
        task = housekeeper_main.parse_task_trigger("把红球送到门口")

        self.assertIsNotNone(task)
        self.assertEqual(task.requested_color, "red")
        self.assertEqual(task.effective_color, "red")
        self.assertEqual(task.target_station, "station_B")
        self.assertEqual(task.station_label, "门口")
        self.assertEqual(task.summary(), "红球 -> 门口")

    def test_task_trigger_keeps_non_red_requests_for_execution(self):
        task = housekeeper_main.parse_task_trigger("把绿色方块送到客厅")

        self.assertIsNotNone(task)
        self.assertEqual(task.requested_color, "green")
        self.assertEqual(task.effective_color, "green")
        self.assertEqual(task.target_station, "station_A")
        self.assertFalse(task.color_downgraded)
        self.assertEqual(task.capability_notice(), "")

    def test_task_trigger_accepts_verb_ball_asr_misrecognitions(self):
        # "见球""捡球"同音,ASR 常见误识别;必须动词+球同时出现
        for text in ("见球", "帮我拿一下球", "去找那个球"):
            with self.subTest(text=text):
                task = housekeeper_main.parse_task_trigger(text)
                self.assertIsNotNone(task)
                self.assertEqual(task.name, "grab_then_follow_line")

    def test_task_trigger_rejects_bare_ball_chatter(self):
        # 单独出现"球"字的闲聊不再触发任务(修复 TTS 回声/闲聊误触发)
        for text in ("眼球", "有序进球", "这里还有一个球"):
            with self.subTest(text=text):
                self.assertIsNone(housekeeper_main.parse_task_trigger(text))

    def test_task_trigger_rejects_motion_or_chatter(self):
        for text in ("前进", "左转", "坐下", "今天天气怎么样", "我放了个东西"):
            with self.subTest(text=text):
                self.assertIsNone(housekeeper_main.parse_task_trigger(text))

    def test_control_command_parses_stop_and_continue(self):
        for text in ("停止", "停下", "急停", "暂停", "停止任务", "暂停任务"):
            with self.subTest(text=text):
                self.assertEqual(housekeeper_main.parse_control_command(text), "stop")

        for text in ("继续", "恢复任务", "接着执行", "继续任务", "恢复执行"):
            with self.subTest(text=text):
                self.assertEqual(housekeeper_main.parse_control_command(text), "continue")

        self.assertIsNone(housekeeper_main.parse_control_command("开始任务"))

    def test_control_command_ignores_stop_negations(self):
        for text in ("别停", "不要停下", "不用停"):
            with self.subTest(text=text):
                self.assertNotEqual(housekeeper_main.parse_control_command(text), "stop")

    def test_echo_guard_blocks_tts_playback_from_triggering_tasks(self):
        clock = [100.0]
        guard = EchoGuard(memory_seconds=12.0, clock=lambda: clock[0])

        guard.begin_speaking("开始执行捡球任务")
        # 播报进行中,ASR 识别回自己的声音
        self.assertTrue(guard.is_echo("开始执行捡球任务"))
        self.assertTrue(guard.is_echo("捡球"))
        guard.end_speaking("开始执行捡球任务")

        # 播完后的记忆窗口内仍然拦截
        clock[0] += 5.0
        self.assertTrue(guard.is_echo("捡球任务"))
        # 与播报内容无关的话不拦截(急停必须能穿透)
        self.assertFalse(guard.is_echo("停止"))
        # 记忆窗口过后放行
        clock[0] += 20.0
        self.assertFalse(guard.is_echo("捡球"))

    def test_augment_command_for_resume_skips_grab_when_ball_already_held(self):
        command = ["/python", "-u", "grab_then_follow_line.py"]
        with tempfile.TemporaryDirectory() as temp_dir:
            result_path = Path(temp_dir) / "grab_result.json"

            # 没有结果文件:原样重跑
            self.assertEqual(
                housekeeper_main.augment_command_for_resume(command, result_path), command
            )

            # 上次抓球成功:直接跳过抓球
            result_path.write_text(json.dumps({"success": True}), encoding="utf-8")
            self.assertEqual(
                housekeeper_main.augment_command_for_resume(command, result_path),
                command + ["--skip-grab"],
            )

            # 上次失败:原样重跑
            result_path.write_text(json.dumps({"success": False}), encoding="utf-8")
            self.assertEqual(
                housekeeper_main.augment_command_for_resume(command, result_path), command
            )

    def test_auth_result_confirms_owner_only_on_owner_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result_path = Path(temp_dir) / "auth_result.json"

            self.assertFalse(housekeeper_main.auth_result_confirms_owner(result_path))

            result_path.write_text(json.dumps({"identity": "owner", "score": 0.8}), encoding="utf-8")
            self.assertTrue(housekeeper_main.auth_result_confirms_owner(result_path))

            result_path.write_text(json.dumps({"identity": "stranger"}), encoding="utf-8")
            self.assertFalse(housekeeper_main.auth_result_confirms_owner(result_path))

    def test_voice_event_parses_control_before_task(self):
        stop_event = housekeeper_main.parse_voice_event("停止")
        self.assertEqual(stop_event.kind, "control")
        self.assertEqual(stop_event.value, "stop")

        task_event = housekeeper_main.parse_voice_event("开始任务")
        self.assertEqual(task_event.kind, "task")
        self.assertEqual(task_event.value.name, "grab_then_follow_line")
        self.assertEqual(task_event.value.target_station, "station_A")

        self.assertIsNone(housekeeper_main.parse_voice_event("今天有点热"))

    def test_voice_event_parses_music_play_and_stop(self):
        play_event = housekeeper_main.parse_voice_event("放歌")
        self.assertEqual(play_event.kind, "music")
        self.assertEqual(play_event.value, "play")

        play_music_event = housekeeper_main.parse_voice_event("播放音乐")
        self.assertEqual(play_music_event.kind, "music")
        self.assertEqual(play_music_event.value, "play")

        stop_music_event = housekeeper_main.parse_voice_event("停歌")
        self.assertEqual(stop_music_event.kind, "music")
        self.assertEqual(stop_music_event.value, "stop")

        stop_event = housekeeper_main.parse_voice_event("停止")
        self.assertEqual(stop_event.kind, "control")
        self.assertEqual(stop_event.value, "stop")

    def test_build_music_player_command_uses_remote_player_script(self):
        command = housekeeper_main.build_music_player_command(
            Path("/python"),
            Path("/home/pi/dogzilla_runs/dogzilla_music_player.py"),
            music_dir=Path("/home/pi/dogzilla_runs/music"),
            song="周杰伦",
            volume=75,
            loop=True,
            action="play",
        )

        self.assertEqual(command[:2], ["/python", "/home/pi/dogzilla_runs/dogzilla_music_player.py"])
        self.assertIn("--music-dir", command)
        self.assertIn("/home/pi/dogzilla_runs/music", command)
        self.assertIn("--song", command)
        self.assertIn("周杰伦", command)
        self.assertIn("--volume", command)
        self.assertIn("75", command)
        self.assertIn("--background", command)
        self.assertIn("--loop", command)

    def test_build_music_player_stop_command(self):
        command = housekeeper_main.build_music_player_command(
            Path("/python"),
            Path("/home/pi/dogzilla_runs/dogzilla_music_player.py"),
            music_dir=Path("/home/pi/dogzilla_runs/music"),
            action="stop",
        )

        self.assertIn("--stop", command)
        self.assertNotIn("--background", command)

    def test_build_music_player_command_chooses_first_song_when_song_is_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            music_dir = Path(temp_dir)
            (music_dir / "b.mp3").write_bytes(b"")
            (music_dir / "a.mp3").write_bytes(b"")

            command = housekeeper_main.build_music_player_command(
                Path("/python"),
                Path("/home/pi/dogzilla_runs/dogzilla_music_player.py"),
                music_dir=music_dir,
                song="",
                action="play",
            )

        self.assertIn("--song", command)
        self.assertIn("a.mp3", command)

    def test_run_music_command_invokes_player_and_speaks_feedback(self):
        calls = []
        speaker = self.FakeSpeaker()
        args = argparse.Namespace(
            python=Path("/python"),
            music_player=Path("/home/pi/dogzilla_runs/dogzilla_music_player.py"),
            music_dir=Path("/home/pi/dogzilla_runs/music"),
            music_song="",
            music_volume=80,
            music_loop=False,
            music_timeout=5.0,
        )

        def runner(command, **_kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        code = housekeeper_main.run_music_command("play", args, speaker=speaker, runner=runner)

        self.assertEqual(code, 0)
        self.assertEqual(speaker.messages, ["开始播放音乐"])
        self.assertEqual(calls[0][:2], ["/python", "/home/pi/dogzilla_runs/dogzilla_music_player.py"])
        self.assertIn("--background", calls[0])

    def test_answer_voice_chat_uses_spark_and_speaks_answer(self):
        speaker = self.FakeSpeaker()
        spark = self.FakeSparkModule(answer="北京是中国的首都。")

        handled = housekeeper_main.answer_voice_chat(
            "中国首都是哪里",
            self.spark_args(),
            speaker,
            voice_module=spark,
        )

        self.assertTrue(handled)
        self.assertEqual(spark.questions, ["中国首都是哪里"])
        self.assertEqual(speaker.messages, ["北京是中国的首都。"])

    def test_answer_voice_chat_ignores_text_when_spark_filter_rejects_it(self):
        speaker = self.FakeSpeaker()
        spark = self.FakeSparkModule(should_chat=False)

        handled = housekeeper_main.answer_voice_chat(
            "今天有点热",
            self.spark_args(),
            speaker,
            voice_module=spark,
        )

        self.assertFalse(handled)
        self.assertEqual(spark.questions, [])
        self.assertEqual(speaker.messages, [])

    def test_answer_voice_chat_reports_spark_failure(self):
        speaker = self.FakeSpeaker()
        spark = self.FakeSparkModule(error=RuntimeError("bad key"))

        handled = housekeeper_main.answer_voice_chat(
            "中国首都是哪里",
            self.spark_args(),
            speaker,
            voice_module=spark,
        )

        self.assertTrue(handled)
        self.assertEqual(speaker.messages, ["这个问题我暂时回答失败了"])

    def test_tts_speaker_prefers_online_human_voice_backend(self):
        online_tts = self.FakeOnlineTts()
        speaker = housekeeper_main.TtsSpeaker(online_tts=online_tts)

        speaker.speak("你好，我是机器狗")

        self.assertEqual(online_tts.messages, ["你好，我是机器狗"])

    def test_runtime_controller_stores_task_from_full_time_voice_listener(self):
        controller = housekeeper_main.RuntimeController()
        task = housekeeper_main.HousekeeperTask("grab_then_follow_line", "开始任务")

        controller.request_task(task)
        received = controller.wait_for_task(timeout_seconds=0.1)

        self.assertEqual(received, task)
        self.assertIsNone(controller.wait_for_task(timeout_seconds=0.01))

    def test_runtime_controller_suppresses_chat_while_child_process_is_active(self):
        controller = housekeeper_main.RuntimeController()
        process = object()

        self.assertTrue(housekeeper_main.should_answer_runtime_chat(controller))
        controller.set_process(process)
        self.assertFalse(housekeeper_main.should_answer_runtime_chat(controller))
        controller.clear_process(process)
        self.assertTrue(housekeeper_main.should_answer_runtime_chat(controller))

    def test_workflow_feedback_from_child_logs(self):
        cases = {
            "=== GRAB ===": "开始抓球",
            "Grab succeeded. Releasing camera before line following.": "捡球成功",
            "=== FIND_AND_ALIGN_LINE ===": "开始寻找黑线",
            "Line alignment succeeded. Starting line following.": "开始巡线",
            "TASK_QR_SCAN_START 请把任务二维码放到摄像头前": "请把任务二维码放到摄像头前",
        }

        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(housekeeper_main.workflow_feedback_for_line(line), expected)

    def test_build_face_auth_command_runs_face_module_unbuffered(self):
        command = housekeeper_main.build_face_auth_command(
            Path("/python"),
            Path("/course/14.housekeeper_minimal"),
            robot_ip="172.20.10.9",
            port=8090,
            extra_args=["--threshold", "0.4"],
        )

        self.assertEqual(command[:3], ["/python", "-u", "/course/14.housekeeper_minimal/face_interaction.py"])
        self.assertIn("--robot-ip", command)
        self.assertIn("172.20.10.9", command)
        self.assertIn("--port", command)
        self.assertIn("8090", command)
        self.assertIn("--exit-on-owner", command)
        self.assertIn("--auth-result", command)
        self.assertEqual(command[-2:], ["--threshold", "0.4"])

    def test_build_grab_workflow_command_runs_existing_minimal_loop(self):
        command = housekeeper_main.build_grab_workflow_command(
            Path("/python"),
            Path("/course/14.housekeeper_minimal"),
            line_seconds=30,
            task=housekeeper_main.HousekeeperTask.for_request(
                raw_text="把绿球送到门口",
                requested_color="green",
                effective_color="green",
                target_station="station_B",
                station_label="门口",
                color_label="绿球",
            ),
            return_home=True,
            delivery_task_mode="qr",
            extra_args=["--stream-grab"],
        )

        self.assertEqual(command[:3], ["/python", "-u", "/course/14.housekeeper_minimal/grab_then_follow_line.py"])
        self.assertIn("--line-seconds", command)
        self.assertIn("30", command)
        self.assertIn("--target-station", command)
        self.assertIn("station_B", command)
        self.assertIn("--target-color", command)
        self.assertIn("green", command)
        self.assertIn("--task-mode", command)
        self.assertIn("qr", command)
        self.assertIn("--return-home", command)
        self.assertEqual(command[-1], "--stream-grab")

    def test_run_sequence_calls_stages_in_order(self):
        calls = []

        def auth():
            calls.append("auth")
            return True

        def listen():
            calls.append("listen")
            return housekeeper_main.HousekeeperTask("grab_then_follow_line", "开始任务")

        def run_task(_task):
            calls.append("task")
            return 0

        speaker = self.FakeSpeaker()

        code = housekeeper_main.run_sequence(auth, listen, run_task, speaker=speaker)

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["auth", "listen", "task"])
        self.assertEqual(
            speaker.messages,
            ["开始识别人脸", "人脸识别成功", "开始听语音指令", "收到,去拿红球送到客厅", "开始执行捡球任务", "任务完成"],
        )

    def test_run_sequence_stops_when_owner_not_confirmed(self):
        calls = []

        def auth():
            calls.append("auth")
            return False

        def listen():
            calls.append("listen")
            return housekeeper_main.HousekeeperTask("grab_then_follow_line", "开始任务")

        def run_task(_task):
            calls.append("task")
            return 0

        code = housekeeper_main.run_sequence(auth, listen, run_task)

        self.assertEqual(code, housekeeper_main.EXIT_AUTH_FAILED)
        self.assertEqual(calls, ["auth"])

    def test_run_sequence_retries_stage_after_global_pause_and_continue(self):
        calls = []
        speaker = self.FakeSpeaker()
        controller = housekeeper_main.RuntimeController(speaker=speaker)
        original_stop_robot_motion = housekeeper_main.stop_robot_motion
        housekeeper_main.stop_robot_motion = lambda **_kwargs: None

        def auth():
            calls.append("auth")
            if calls.count("auth") == 1:
                controller.request_stop()
                controller.request_continue()
                return False
            return True

        def listen():
            calls.append("listen")
            return housekeeper_main.HousekeeperTask("grab_then_follow_line", "开始任务")

        def run_task(_task):
            calls.append("task")
            return 0

        try:
            code = housekeeper_main.run_sequence(
                auth,
                listen,
                run_task,
                speaker=speaker,
                controller=controller,
            )
        finally:
            housekeeper_main.stop_robot_motion = original_stop_robot_motion

        self.assertEqual(code, 0)
        self.assertEqual(calls, ["auth", "auth", "listen", "task"])
        self.assertNotIn("人脸识别失败", speaker.messages)

    def test_wait_for_task_loop_retries_after_voice_error(self):
        calls = []

        def listen_once():
            calls.append("listen")
            if len(calls) == 1:
                raise TimeoutError("handshake timed out")
            return "眼球", housekeeper_main.HousekeeperTask("grab_then_follow_line", "眼球")

        ticks = iter([0.0, 0.1, 0.2])
        task = housekeeper_main.wait_for_task_loop(
            listen_once,
            timeout_seconds=5,
            retry_delay=0,
            clock=lambda: next(ticks),
            sleeper=lambda _seconds: None,
        )

        self.assertEqual(task.name, "grab_then_follow_line")
        self.assertEqual(calls, ["listen", "listen"])


if __name__ == "__main__":
    unittest.main()
