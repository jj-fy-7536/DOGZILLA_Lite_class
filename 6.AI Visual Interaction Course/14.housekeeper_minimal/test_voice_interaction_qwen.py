import argparse
import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent))

import voice_interaction


class VoiceInteractionQwenTest(unittest.TestCase):
    def qwen_args(self, **overrides):
        defaults = {
            "qwen_chat": True,
            "qwen_api_key": "qwen-key",
            "qwen_api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen_model": "qwen-plus",
            "qwen_system_prompt": "你是机器狗助手。",
            "qwen_temperature": 0.45,
            "qwen_max_tokens": 180,
            "qwen_timeout": 10.0,
            "qwen_search_timeout": 25.0,
            "qwen_max_reply_chars": 110,
            "qwen_min_chars": 2,
            "qwen_web_search": True,
            "qwen_always_search": False,
            "qwen_question_only": False,
            "spark_chat": True,
            "spark_api_password": "",
            "spark_api_url": "https://spark-api-open.xf-yun.com/v1/chat/completions",
            "spark_model": "lite",
            "spark_system_prompt": "你是机器狗助手。",
            "spark_temperature": 0.5,
            "spark_max_tokens": 160,
            "spark_timeout": 8.0,
            "spark_max_reply_chars": 90,
            "spark_min_chars": 3,
            "spark_question_only": True,
        }
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_chat_backend_available_accepts_qwen_or_spark(self):
        self.assertTrue(voice_interaction.chat_backend_available(self.qwen_args()))
        self.assertTrue(
            voice_interaction.chat_backend_available(
                self.qwen_args(qwen_api_key="", spark_api_password="spark-secret")
            )
        )
        self.assertFalse(
            voice_interaction.chat_backend_available(
                self.qwen_args(qwen_api_key="", spark_api_password="", qwen_chat=False, spark_chat=False)
            )
        )

    def test_should_use_qwen_chat_respects_question_only(self):
        args = self.qwen_args(qwen_question_only=True)
        self.assertTrue(voice_interaction.should_use_qwen_chat("今天天气怎么样？", args))
        self.assertFalse(voice_interaction.should_use_qwen_chat("今天有点热", args))

    def test_should_use_spark_chat_uses_qwen_threshold_when_qwen_configured(self):
        args = self.qwen_args(qwen_min_chars=2, spark_min_chars=5)
        self.assertTrue(voice_interaction.should_use_spark_chat("你好呀", args))

    def test_fetch_spark_answer_prefers_qwen_when_configured(self):
        args = self.qwen_args()
        with mock.patch.object(
            voice_interaction,
            "fetch_qwen_answer",
            return_value="Qwen 回答。",
        ) as fetch_qwen:
            answer = voice_interaction.fetch_spark_answer("你是谁？", args)
        self.assertEqual(answer, "Qwen 回答。")
        fetch_qwen.assert_called_once_with("你是谁？", args)

    def test_fetch_spark_answer_falls_back_to_spark_when_qwen_fails(self):
        args = self.qwen_args(spark_api_password="spark-secret")
        with mock.patch.object(
            voice_interaction,
            "fetch_qwen_answer",
            side_effect=RuntimeError("qwen down"),
        ), mock.patch.object(
            voice_interaction,
            "fetch_spark_lite_answer",
            return_value="Spark 回答。",
        ) as fetch_spark:
            answer = voice_interaction.fetch_spark_answer("你是谁？", args)
        self.assertEqual(answer, "Spark 回答。")
        fetch_spark.assert_called_once_with("你是谁？", args)

    def test_should_use_qwen_web_search_for_realtime_questions(self):
        args = self.qwen_args()
        self.assertTrue(voice_interaction.should_use_qwen_web_search("今天世界杯比分是多少？", args))
        self.assertFalse(voice_interaction.should_use_qwen_web_search("背一首静夜思", args))

    def test_parse_command_skips_local_weather_when_disabled(self):
        with mock.patch.object(voice_interaction, "LOCAL_WEATHER_ENABLED", False):
            command = voice_interaction.parse_command("北京天气怎么样")
        self.assertIsNone(command)

    def test_stop_other_robot_tasks_terminates_other_scripts(self):
        args = self.qwen_args(
            stop_kill_tasks=True,
            stop_kill_dirs="/home/pi/dogzilla_runs",
            stop_kill_patterns="housekeeper_main",
            stop_kill_wait=0.0,
        )
        victims = [(222, "python3 /home/pi/dogzilla_runs/housekeeper_main.py")]
        with mock.patch.object(voice_interaction.os.path, "isdir", return_value=True), mock.patch.object(
            voice_interaction,
            "find_other_robot_task_pids",
            return_value=victims,
        ), mock.patch.object(voice_interaction.os, "kill") as kill, mock.patch.object(
            voice_interaction,
            "process_alive",
            return_value=False,
        ):
            voice_interaction.stop_other_robot_tasks(args)
        kill.assert_called_once_with(222, voice_interaction.signal.SIGTERM)

    def test_find_other_robot_task_pids_skips_voice_process(self):
        args = self.qwen_args(
            stop_kill_dirs="/home/pi/dogzilla_runs",
            stop_kill_patterns="housekeeper_main,voice_interaction",
        )

        def fake_read_cmdline(pid):
            if pid == 101:
                return ["/usr/bin/python3", "/home/pi/dogzilla_runs/voice_interaction.py"]
            if pid == 202:
                return ["/usr/bin/python3", "/home/pi/dogzilla_runs/housekeeper_main.py"]
            return []

        with mock.patch.object(voice_interaction.os, "getpid", return_value=1), mock.patch.object(
            voice_interaction.os,
            "getppid",
            return_value=0,
        ), mock.patch.object(voice_interaction.os, "listdir", return_value=["101", "202"]), mock.patch.object(
            voice_interaction,
            "read_process_cmdline",
            side_effect=fake_read_cmdline,
        ):
            victims = voice_interaction.find_other_robot_task_pids(args)

        self.assertEqual(victims, [(202, "/usr/bin/python3 /home/pi/dogzilla_runs/housekeeper_main.py")])

    def test_is_voice_process_recognizes_voice_interaction_script(self):
        parts = ["/usr/bin/python3", "/home/pi/dogzilla_runs/voice_interaction.py"]
        self.assertTrue(voice_interaction.is_voice_process(parts))


if __name__ == "__main__":
    unittest.main()
