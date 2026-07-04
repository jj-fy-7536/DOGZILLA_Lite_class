import argparse
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.modules.setdefault("websocket", types.SimpleNamespace(WebSocketApp=object))

MODULE_PATH = Path(__file__).with_name("voice_interaction.py")
SPEC = importlib.util.spec_from_file_location("voice_interaction", MODULE_PATH)
voice_interaction = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = voice_interaction
SPEC.loader.exec_module(voice_interaction)


def chat_args(**overrides):
    values = {
        "spark_chat": True,
        "spark_api_password": "deepseek-key",
        "spark_min_chars": 3,
        "web_search": True,
        "web_search_url": "auto",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class VoiceInteractionChatFilterTests(unittest.TestCase):
    def test_noise_fragments_do_not_enter_chat(self):
        args = chat_args()

        for text in ("嗯嗯嗯", "啊啊啊", "好的", "abc", "哦"):
            with self.subTest(text=text):
                self.assertFalse(voice_interaction.should_use_spark_chat(text, args))

    def test_weather_and_score_questions_still_enter_chat(self):
        args = chat_args()

        for text in ("杭州天气怎么样", "阿根廷和佛得角比分是多少"):
            with self.subTest(text=text):
                self.assertTrue(voice_interaction.should_use_spark_chat(text, args))

    def test_weather_text_is_not_a_builtin_command(self):
        command = voice_interaction.parse_command("今天天气怎么样")

        self.assertIsNone(command)
        self.assertTrue(voice_interaction.should_use_spark_chat("今天天气怎么样", chat_args()))

    def test_world_cup_search_uses_score_sources_and_date_queries(self):
        urls = voice_interaction.build_web_search_urls(
            voice_interaction.normalize_web_search_query("那今天世界杯比赛不是有了吗"),
            "auto",
        )
        joined = "\n".join(url for _label, url, _html_mode in urls)

        self.assertIn("foxsports.com/soccer/fifa-world-cup/scores", joined)
        self.assertIn("espn.com/soccer/schedule", joined)
        self.assertIn("FIFA%20World%20Cup%20fixtures%20scores%20today", joined)
        self.assertIn("%E4%B8%96%E7%95%8C%E6%9D%AF%20%E8%B5%9B%E7%A8%8B%20%E6%AF%94%E5%88%86", joined)

    def test_web_search_context_combines_multiple_sources(self):
        args = chat_args(web_search_timeout=1.0, web_search_max_chars=1000, web_search_max_sources=2)

        class FakeHeaders:
            def get_content_charset(self):
                return "utf-8"

        class FakeResponse:
            headers = FakeHeaders()

            def __init__(self, text):
                self.text = text

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return self.text.encode("utf-8")

        def fake_urlopen(request, timeout):
            url = request.full_url
            if "foxsports.com" in url:
                return FakeResponse("<html>Today ROUND OF 16 Canada CAN Morocco MAR Paraguay PAR France FRA</html>")
            if "espn.com" in url:
                return FakeResponse("<html>Saturday July 4 Paraguay v France Canada v Morocco</html>")
            return FakeResponse("")

        with mock.patch.object(voice_interaction, "urlopen", side_effect=fake_urlopen):
            context = voice_interaction.fetch_web_search_context("那今天世界杯比赛不是有了吗", args)

        self.assertIn("FOX世界杯比分搜索结果", context)
        self.assertIn("Canada", context)
        self.assertIn("ESPN世界杯赛程搜索结果", context)
        self.assertIn("Paraguay", context)


if __name__ == "__main__":
    unittest.main()
