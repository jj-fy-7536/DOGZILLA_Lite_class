import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("housekeeper_config.py")
SPEC = importlib.util.spec_from_file_location("housekeeper_config", MODULE_PATH)
housekeeper_config = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = housekeeper_config
SPEC.loader.exec_module(housekeeper_config)


class HousekeeperConfigTests(unittest.TestCase):
    def test_default_config_resolves_color_and_station_aliases(self):
        config = housekeeper_config.HousekeeperConfig.default()

        self.assertEqual(config.resolve_color("红色方块"), "red")
        self.assertEqual(config.resolve_color("绿球"), "green")
        self.assertEqual(config.resolve_station("门口"), "station_B")
        self.assertEqual(config.station_label("station_B"), "门口")
        self.assertEqual(config.color_label("red"), "红球")

    def test_missing_config_file_uses_defaults(self):
        config = housekeeper_config.load_config(Path("/missing/housekeeper_config.json"))

        self.assertEqual(config.defaults.color, "red")
        self.assertEqual(config.defaults.target_station, "station_A")
        self.assertTrue(config.return_home.enabled)

    def test_load_config_merges_user_station_aliases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "housekeeper_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "stations": {
                            "station_C": {
                                "label": "书房",
                                "aliases": ["书房", "C点"],
                            }
                        },
                        "defaults": {"target_station": "station_C"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            config = housekeeper_config.load_config(config_path)

        self.assertEqual(config.resolve_station("送到书房"), "station_C")
        self.assertEqual(config.station_label("station_C"), "书房")
        self.assertEqual(config.defaults.color, "red")
        self.assertEqual(config.defaults.target_station, "station_C")

    def test_invalid_config_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "housekeeper_config.json"
            config_path.write_text("{bad json", encoding="utf-8")

            with self.assertRaises(housekeeper_config.ConfigError):
                housekeeper_config.load_config(config_path)


if __name__ == "__main__":
    unittest.main()
