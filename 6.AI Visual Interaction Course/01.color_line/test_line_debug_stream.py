import importlib.util
import pathlib
import unittest

import numpy as np


SCRIPT_PATH = pathlib.Path(__file__).with_name("line_debug_stream.py")
SPEC = importlib.util.spec_from_file_location("line_debug_stream", SCRIPT_PATH)
line_debug_stream = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(line_debug_stream)


class LineDebugStreamTest(unittest.TestCase):
    def test_index_page_points_to_mjpeg_stream(self):
        html = line_debug_stream.build_index_html("/stream.mjpg", "black")

        self.assertIn("DOGZILLA Line Debug Stream", html)
        self.assertIn('src="/stream.mjpg"', html)
        self.assertIn("black", html)

    def test_mjpeg_frame_format(self):
        chunk = line_debug_stream.format_mjpeg_frame(b"abc", boundary="frame")

        self.assertTrue(chunk.startswith(b"--frame\r\n"))
        self.assertIn(b"Content-Type: image/jpeg\r\n", chunk)
        self.assertIn(b"Content-Length: 3\r\n\r\nabc\r\n", chunk)

    def test_select_line_point_matches_requested_color(self):
        point = line_debug_stream.select_line_point(
            ["red", "black", "blue"], [[10, 20], [160, 180], [300, 40]], "black"
        )

        self.assertEqual(point, [160, 180])
        self.assertIsNone(line_debug_stream.select_line_point(["red"], [[10, 20]], "black"))

    def test_debug_stream_module_has_no_standalone_camera_runner(self):
        self.assertFalse(hasattr(line_debug_stream, "LineVisionWorker"))
        self.assertFalse(hasattr(line_debug_stream, "run_server"))
        self.assertFalse(hasattr(line_debug_stream, "parse_args"))

    def test_rgb_frame_encoding_keeps_red_red(self):
        try:
            import cv2
        except ImportError:
            self.skipTest("cv2 is only available on the robot runtime")

        frame = np.zeros((20, 20, 3), dtype=np.uint8)
        frame[:, :] = [255, 0, 0]

        jpeg = line_debug_stream.encode_rgb_frame_to_jpeg(frame, jpeg_quality=95)
        decoded = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        b, g, r = decoded[10, 10]

        self.assertGreater(r, 200)
        self.assertLess(b, 50)

    def test_rgb_frame_encoding_keeps_blue_blue(self):
        try:
            import cv2
        except ImportError:
            self.skipTest("cv2 is only available on the robot runtime")

        frame = np.zeros((20, 20, 3), dtype=np.uint8)
        frame[:, :] = [0, 0, 255]

        jpeg = line_debug_stream.encode_rgb_frame_to_jpeg(frame, jpeg_quality=95)
        decoded = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        b, g, r = decoded[10, 10]

        self.assertGreater(b, 200)
        self.assertLess(r, 50)


if __name__ == "__main__":
    unittest.main()
