import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np


MODULE_PATH = Path(__file__).with_name("find_and_align_line.py")
SPEC = importlib.util.spec_from_file_location("find_and_align_line", MODULE_PATH)
find_and_align_line = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = find_and_align_line
SPEC.loader.exec_module(find_and_align_line)


class FindAndAlignLineTests(unittest.TestCase):
    def test_black_mask_uses_dark_neutral_pixels(self):
        frame = np.zeros((2, 3, 3), dtype=np.uint8)
        frame[0, 0] = [20, 20, 20]
        frame[0, 1] = [20, 70, 20]
        frame[0, 2] = [120, 120, 120]

        mask = find_and_align_line.black_mask_from_rgb(frame)

        self.assertTrue(mask[0, 0])
        self.assertFalse(mask[0, 1])
        self.assertFalse(mask[0, 2])

    def test_best_candidate_prefers_bottom_center_tape_over_edge_noise(self):
        mask = np.zeros((240, 320), dtype=bool)
        mask[205:235, 145:175] = True
        mask[205:235, 0:18] = True
        mask[140:165, 240:285] = True

        candidate = find_and_align_line.find_best_line_candidate(mask)

        self.assertTrue(candidate.found)
        self.assertEqual(candidate.x, 159)
        self.assertGreater(candidate.y, 200)
        self.assertGreater(candidate.score, 0)

    def test_alignment_turns_toward_visible_line_and_accepts_centered_line(self):
        left = find_and_align_line.LineCandidate(True, x=95, y=214, width=30, score=100)
        right = find_and_align_line.LineCandidate(True, x=225, y=214, width=30, score=100)
        centered = find_and_align_line.LineCandidate(True, x=161, y=214, width=30, score=100)

        self.assertEqual(find_and_align_line.turn_for_candidate(left), find_and_align_line.ALIGN_TURN)
        self.assertEqual(find_and_align_line.turn_for_candidate(right), -find_and_align_line.ALIGN_TURN)
        self.assertEqual(find_and_align_line.turn_for_candidate(centered), 0)

    def test_centered_line_is_ready_even_when_not_near_bottom(self):
        candidate = find_and_align_line.LineCandidate(True, x=160, y=105, width=30, score=250)

        self.assertTrue(
            find_and_align_line.alignment_ready(
                candidate,
                stable_count=find_and_align_line.ALIGN_STABLE_FRAMES,
                tolerance=find_and_align_line.ALIGN_TOLERANCE,
                min_score=120,
            )
        )

    def test_line_search_pose_is_normal_standing_not_crouched(self):
        class FakeDog:
            def __init__(self):
                self.calls = []

            def translation(self, axis, values):
                self.calls.append(("translation", axis, values))

            def attitude(self, axis, values):
                self.calls.append(("attitude", axis, values))

            def pace(self, value):
                self.calls.append(("pace", value))

        dog = FakeDog()

        find_and_align_line.configure_line_search_pose(dog)

        self.assertIn(("translation", ["z"], [100]), dog.calls)
        self.assertIn(("attitude", ["p"], [0]), dog.calls)
        self.assertNotIn(("translation", ["z"], [75]), dog.calls)
        self.assertNotIn(("attitude", ["p"], [15]), dog.calls)


if __name__ == "__main__":
    unittest.main()
