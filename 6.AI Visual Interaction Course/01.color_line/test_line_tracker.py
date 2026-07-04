import unittest

import numpy as np

import line_tracker


class LineTrackerTest(unittest.TestCase):
    def test_straight_line_uses_bottom_scan_band(self):
        binary = np.zeros((240, 320), dtype=np.uint8)
        binary[:, 150:171] = 255

        decision = line_tracker.choose_line_target(binary, last_x=160)

        self.assertTrue(decision.found)
        self.assertEqual(decision.mode, "straight")
        self.assertGreaterEqual(decision.x, 150)
        self.assertLessEqual(decision.x, 170)
        self.assertEqual(decision.speed, line_tracker.STRAIGHT_SPEED)

    def test_right_angle_prefers_visible_right_branch(self):
        binary = np.zeros((240, 320), dtype=np.uint8)
        binary[80:240, 150:171] = 255
        binary[45:66, 160:306] = 255

        decision = line_tracker.choose_line_target(binary, last_x=160)

        self.assertTrue(decision.found)
        self.assertEqual(decision.mode, "corner_right")
        self.assertGreaterEqual(decision.x, 280)
        self.assertEqual(decision.speed, line_tracker.CORNER_SPEED)
        self.assertGreater(decision.turn_multiplier, 1.0)

    def test_left_angle_prefers_visible_left_branch(self):
        binary = np.zeros((240, 320), dtype=np.uint8)
        binary[80:240, 150:171] = 255
        binary[45:66, 14:160] = 255

        decision = line_tracker.choose_line_target(binary, last_x=160)

        self.assertTrue(decision.found)
        self.assertEqual(decision.mode, "corner_left")
        self.assertLessEqual(decision.x, 40)
        self.assertEqual(decision.speed, line_tracker.CORNER_SPEED)

    def test_bottom_noise_far_from_last_center_is_ignored_when_center_line_exists(self):
        binary = np.zeros((240, 320), dtype=np.uint8)
        binary[200:240, 150:171] = 255
        binary[200:240, 5:25] = 255

        decision = line_tracker.choose_line_target(binary, last_x=160)

        self.assertTrue(decision.found)
        self.assertEqual(decision.mode, "straight")
        self.assertGreaterEqual(decision.x, 150)
        self.assertLessEqual(decision.x, 170)

    def test_curve_uses_multiple_bands_and_slows_down(self):
        binary = np.zeros((240, 320), dtype=np.uint8)
        binary[205:235, 260:282] = 255
        binary[165:195, 215:237] = 255
        binary[120:150, 165:187] = 255

        decision = line_tracker.choose_line_target(binary, last_x=160)

        self.assertTrue(decision.found)
        self.assertEqual(decision.mode, "curve")
        self.assertLess(decision.speed, line_tracker.STRAIGHT_SPEED)
        self.assertGreater(decision.x, 180)
        self.assertLess(decision.x, 250)

    def test_tracker_accepts_curve_across_jump_filter(self):
        tracker = line_tracker.LineTracker()
        binary = np.zeros((240, 320), dtype=np.uint8)
        binary[205:235, 260:282] = 255
        binary[165:195, 215:237] = 255
        binary[120:150, 165:187] = 255

        decision = tracker.decide(binary, last_x=160)

        self.assertTrue(decision.found)
        self.assertEqual(decision.mode, "curve")
        self.assertLess(decision.speed, line_tracker.STRAIGHT_SPEED)


class CornerLockTest(unittest.TestCase):
    @staticmethod
    def _corner_right_frame():
        binary = np.zeros((240, 320), dtype=np.uint8)
        binary[80:240, 150:171] = 255
        binary[45:66, 160:306] = 255
        return binary

    @staticmethod
    def _edge_noise_frame():
        #弯道中途：底部只剩画面左缘的深色干扰 Mid-turn: only dark noise at the left edge remains
        binary = np.zeros((240, 320), dtype=np.uint8)
        binary[190:240, 0:30] = 255
        return binary

    @staticmethod
    def _center_line_frame():
        binary = np.zeros((240, 320), dtype=np.uint8)
        binary[:, 150:171] = 255
        return binary

    def test_lock_holds_turn_against_edge_noise(self):
        tracker = line_tracker.LineTracker()

        decision = tracker.decide(self._corner_right_frame(), last_x=160)
        self.assertEqual(decision.mode, "corner_right")

        for _ in range(5):
            decision = tracker.decide(self._edge_noise_frame(), last_x=decision.x)
            self.assertEqual(decision.mode, "corner_lock_right")
            self.assertLess(decision.turn_override, 0)
            self.assertEqual(decision.speed, line_tracker.CORNER_SPEED)

    def test_lock_releases_on_stable_center_line(self):
        tracker = line_tracker.LineTracker()

        decision = tracker.decide(self._corner_right_frame(), last_x=160)
        self.assertEqual(decision.mode, "corner_right")

        modes = []
        for _ in range(line_tracker.CORNER_LOCK_EXIT_STABLE_FRAMES):
            decision = tracker.decide(self._center_line_frame(), last_x=decision.x)
            modes.append(decision.mode)

        self.assertEqual(modes[-1], "straight")
        self.assertTrue(all(m == "corner_lock_right" for m in modes[:-1]))

    def test_lock_expires_after_max_frames(self):
        tracker = line_tracker.LineTracker()

        decision = tracker.decide(self._corner_right_frame(), last_x=160)
        for _ in range(line_tracker.CORNER_LOCK_FRAMES):
            decision = tracker.decide(self._edge_noise_frame(), last_x=decision.x)
            self.assertEqual(decision.mode, "corner_lock_right")

        #锁定帧耗尽后恢复普通检测 Normal detection resumes once the lock expires
        decision = tracker.decide(self._edge_noise_frame(), last_x=decision.x)
        self.assertNotEqual(decision.mode, "corner_lock_right")


class StraightJumpFilterTest(unittest.TestCase):
    def test_far_jump_is_rejected_until_reacquire(self):
        tracker = line_tracker.LineTracker()
        binary = np.zeros((240, 320), dtype=np.uint8)
        binary[190:240, 40:60] = 255  #远离上次位置但不贴边的色块 A far, non-edge blob

        for _ in range(line_tracker.REACQUIRE_AFTER_REJECTS - 1):
            decision = tracker.decide(binary, last_x=160)
            self.assertFalse(decision.found)

        #连续拒绝到阈值后重新接受，避免永久停住 Re-acquire after enough rejects so it never stalls
        decision = tracker.decide(binary, last_x=160)
        self.assertTrue(decision.found)

    def test_near_line_still_tracked_normally(self):
        tracker = line_tracker.LineTracker()
        binary = np.zeros((240, 320), dtype=np.uint8)
        binary[:, 150:171] = 255

        decision = tracker.decide(binary, last_x=160)
        self.assertTrue(decision.found)
        self.assertEqual(decision.mode, "straight")


class ShadowRejectTest(unittest.TestCase):
    def test_edge_shadow_never_selected(self):
        tracker = line_tracker.LineTracker()
        binary = np.zeros((240, 320), dtype=np.uint8)
        binary[190:240, 0:30] = 255      #左缘阴影 Left-edge shadow
        binary[190:240, 300:320] = 255   #右缘阴影 Right-edge shadow

        decision = tracker.decide(binary, last_x=160)
        self.assertFalse(decision.found)

    def test_center_line_wins_over_edge_shadow(self):
        tracker = line_tracker.LineTracker()
        binary = np.zeros((240, 320), dtype=np.uint8)
        binary[:, 150:171] = 255
        binary[190:240, 0:30] = 255

        decision = tracker.decide(binary, last_x=160)
        self.assertEqual(decision.mode, "straight")
        self.assertGreaterEqual(decision.x, 150)
        self.assertLessEqual(decision.x, 170)

    def test_oversized_blob_rejected_as_shadow(self):
        tracker = line_tracker.LineTracker()
        binary = np.zeros((240, 320), dtype=np.uint8)
        binary[190:240, 60:260] = 255  #200px宽的大阴影 A 200px-wide shadow blob

        decision = tracker.decide(binary, last_x=160)
        self.assertFalse(decision.found)


class SearchTest(unittest.TestCase):
    @staticmethod
    def _line_frame(col_start, col_end):
        binary = np.zeros((240, 320), dtype=np.uint8)
        binary[:, col_start:col_end] = 255
        return binary

    @staticmethod
    def _empty_frame():
        return np.zeros((240, 320), dtype=np.uint8)

    def test_search_starts_toward_last_seen_side(self):
        tracker = line_tracker.LineTracker()

        #先在画面右侧看到线 Line last seen on the right side
        decision = tracker.decide(self._line_frame(200, 221), last_x=160)
        self.assertTrue(decision.found)

        #丢线累计到阈值后开始向右搜索 Search starts toward the right after enough lost frames
        for _ in range(line_tracker.LOST_FRAMES_BEFORE_SEARCH - 1):
            decision = tracker.decide(self._empty_frame(), last_x=decision.x)
            self.assertFalse(decision.found)

        decision = tracker.decide(self._empty_frame(), last_x=decision.x)
        self.assertEqual(decision.mode, "search_right")
        self.assertLess(decision.turn_override, 0)
        self.assertEqual(decision.speed, 0)

    def test_search_reverses_direction_then_gives_up(self):
        tracker = line_tracker.LineTracker()
        tracker.decide(self._line_frame(200, 221), last_x=160)

        modes = []
        for _ in range(line_tracker.LOST_FRAMES_BEFORE_SEARCH + line_tracker.SEARCH_SWEEP_FRAMES * 3):
            decision = tracker.decide(self._empty_frame(), last_x=160)
            modes.append(decision.mode)

        self.assertIn("search_right", modes)
        self.assertIn("search_left", modes)  #第一段扫完后反向 Reverses after the first sweep
        self.assertEqual(modes[-1], "not_found")  #扫完仍没找到则放弃 Gives up after the full sweep

        #放弃后不再自动打转 No more spinning after giving up
        decision = tracker.decide(self._empty_frame(), last_x=160)
        self.assertFalse(decision.found)

    def test_search_reacquires_line_immediately(self):
        tracker = line_tracker.LineTracker()
        tracker.decide(self._line_frame(200, 221), last_x=160)

        for _ in range(line_tracker.LOST_FRAMES_BEFORE_SEARCH + 2):
            decision = tracker.decide(self._empty_frame(), last_x=160)
        self.assertTrue(decision.mode.startswith("search"))

        #线重新出现，立即恢复巡线 Line reappears: resume tracking immediately
        decision = tracker.decide(self._line_frame(150, 171), last_x=160)
        self.assertEqual(decision.mode, "straight")

    def test_lock_expiry_hands_over_to_search(self):
        tracker = line_tracker.LineTracker()
        binary = np.zeros((240, 320), dtype=np.uint8)
        binary[80:240, 150:171] = 255
        binary[45:66, 160:306] = 255

        decision = tracker.decide(binary, last_x=160)
        self.assertEqual(decision.mode, "corner_right")

        #锁定期一直没接上线 Lock runs out without reacquiring the line
        for _ in range(line_tracker.CORNER_LOCK_FRAMES):
            decision = tracker.decide(self._empty_frame(), last_x=decision.x)
            self.assertEqual(decision.mode, "corner_lock_right")

        #锁定耗尽后无缝转入同方向搜索 Seamlessly hands over to same-direction search
        decision = tracker.decide(self._empty_frame(), last_x=decision.x)
        self.assertEqual(decision.mode, "search_right")
        self.assertLess(decision.turn_override, 0)


if __name__ == "__main__":
    unittest.main()
