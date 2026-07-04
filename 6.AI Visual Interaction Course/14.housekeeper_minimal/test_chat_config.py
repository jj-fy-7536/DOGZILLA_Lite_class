import importlib.util
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

MODULE_PATH = Path(__file__).with_name("chat_config.py")
SPEC = importlib.util.spec_from_file_location("chat_config", MODULE_PATH)
chat_config = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = chat_config
SPEC.loader.exec_module(chat_config)


class ChatConfigTests(unittest.TestCase):
    def test_deepseek_key_is_preferred_with_current_defaults(self):
        defaults = chat_config.resolve_chat_defaults({"DEEPSEEK_API_KEY": "deepseek-key"})

        self.assertEqual(defaults.provider, "deepseek")
        self.assertEqual(defaults.api_key, "deepseek-key")
        self.assertEqual(defaults.api_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(defaults.model, "deepseek-v4-flash")

    def test_deepseek_url_and_model_can_be_overridden(self):
        defaults = chat_config.resolve_chat_defaults(
            {
                "DEEPSEEK_API_KEY": "deepseek-key",
                "DEEPSEEK_API_URL": "https://example.test/chat/completions",
                "DEEPSEEK_MODEL": "deepseek-v4-pro",
            }
        )

        self.assertEqual(defaults.provider, "deepseek")
        self.assertEqual(defaults.api_url, "https://example.test/chat/completions")
        self.assertEqual(defaults.model, "deepseek-v4-pro")

    def test_spark_settings_remain_supported_when_deepseek_is_absent(self):
        defaults = chat_config.resolve_chat_defaults(
            {
                "SPARK_API_PASSWORD": "spark-key",
                "SPARK_API_URL": "https://spark.example.test/v1/chat/completions",
                "SPARK_MODEL": "lite",
            }
        )

        self.assertEqual(defaults.provider, "spark")
        self.assertEqual(defaults.api_key, "spark-key")
        self.assertEqual(defaults.api_url, "https://spark.example.test/v1/chat/completions")
        self.assertEqual(defaults.model, "lite")


if __name__ == "__main__":
    unittest.main()
