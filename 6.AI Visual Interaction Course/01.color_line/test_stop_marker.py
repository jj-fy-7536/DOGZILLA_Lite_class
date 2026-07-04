import unittest

import numpy as np

import stop_marker


class StopMarkerTest(unittest.TestCase):
    def test_narrow_line_is_not_stop_marker(self):
        binary = np.zeros((60, 320), dtype=np.uint8)
        binary[:, 150:171] = 255

        self.assertFalse(stop_marker.is_stop_marker(binary))

    def test_large_bottom_black_block_is_stop_marker(self):
        binary = np.zeros((60, 320), dtype=np.uint8)
        binary[10:60, 30:290] = 255

        self.assertTrue(stop_marker.is_stop_marker(binary))

    def test_consecutive_detector_requires_stable_frames(self):
        detector = stop_marker.StopMarkerDetector(required_frames=3)
        marker = np.zeros((60, 320), dtype=np.uint8)
        marker[10:60, 30:290] = 255

        self.assertFalse(detector.update(marker))
        self.assertFalse(detector.update(marker))
        self.assertTrue(detector.update(marker))

    def test_detector_resets_when_marker_disappears(self):
        detector = stop_marker.StopMarkerDetector(required_frames=2)
        marker = np.zeros((60, 320), dtype=np.uint8)
        marker[10:60, 30:290] = 255
        empty = np.zeros((60, 320), dtype=np.uint8)

        self.assertFalse(detector.update(marker))
        self.assertFalse(detector.update(empty))
        self.assertFalse(detector.update(marker))


if __name__ == "__main__":
    unittest.main()
