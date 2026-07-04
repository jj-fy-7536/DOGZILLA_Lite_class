import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("station_navigation.py")
SPEC = importlib.util.spec_from_file_location("station_navigation", MODULE_PATH)
station_navigation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = station_navigation
SPEC.loader.exec_module(station_navigation)


class StationNavigationTests(unittest.TestCase):
    def test_station_decision_reaches_target_station(self):
        decision = station_navigation.station_decision(["station_A", "station_B"], "station_B")

        self.assertTrue(decision.reached)
        self.assertEqual(decision.station, "station_B")
        self.assertEqual(decision.log_line, "STATION_REACHED station_B")

    def test_station_decision_logs_non_target_station(self):
        decision = station_navigation.station_decision(["station_A"], "station_B")

        self.assertFalse(decision.reached)
        self.assertEqual(decision.station, "station_A")
        self.assertEqual(decision.log_line, "STATION_SEEN station_A")

    def test_station_decision_ignores_when_target_missing_or_no_codes(self):
        self.assertIsNone(station_navigation.station_decision(["station_A"], ""))
        self.assertIsNone(station_navigation.station_decision([], "station_B"))

    def test_write_line_result_creates_json_result_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            result_path = Path(temp_dir) / "line_result.json"

            station_navigation.write_line_result(
                result_path,
                success=True,
                target_station="station_B",
                reached_station="station_B",
                mode="outbound",
                timestamp="20260703_213000",
            )

            payload = result_path.read_text(encoding="utf-8")

        self.assertIn('"success": true', payload)
        self.assertIn('"target_station": "station_B"', payload)
        self.assertIn('"mode": "outbound"', payload)


if __name__ == "__main__":
    unittest.main()
