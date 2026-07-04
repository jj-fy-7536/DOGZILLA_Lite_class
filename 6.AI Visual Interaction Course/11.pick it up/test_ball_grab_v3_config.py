import importlib.util
import sys
import types
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("ball_grab_v3.py")
SPEC = importlib.util.spec_from_file_location("ball_grab_v3", MODULE_PATH)
ball_grab_v3 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ball_grab_v3
sys.modules.setdefault("cv2", types.ModuleType("cv2"))
SPEC.loader.exec_module(ball_grab_v3)


class BallGrabConfigTests(unittest.TestCase):
    def test_default_max_steps_allows_longer_search_and_approach(self):
        self.assertEqual(ball_grab_v3.DEFAULT_CONFIG["max_steps"], 200)

    def test_default_target_color_is_red(self):
        self.assertEqual(ball_grab_v3.DEFAULT_CONFIG["target_color"], "red")

    def test_color_ranges_include_course_thresholds_for_four_colors(self):
        self.assertEqual(
            ball_grab_v3.color_ranges_for("green"),
            [((35, 43, 46), (77, 255, 255))],
        )
        self.assertEqual(
            ball_grab_v3.color_ranges_for("blue"),
            [((100, 43, 46), (124, 255, 255))],
        )
        self.assertEqual(
            ball_grab_v3.color_ranges_for("yellow"),
            [((26, 43, 46), (34, 255, 255))],
        )
        self.assertEqual(len(ball_grab_v3.color_ranges_for("red")), 2)


if __name__ == "__main__":
    unittest.main()
