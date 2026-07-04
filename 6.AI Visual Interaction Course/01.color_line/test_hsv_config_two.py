import unittest

import numpy as np

try:
    from HSV_Config_Two import update_hsv
except ModuleNotFoundError as exc:
    if exc.name != "cv2":
        raise
    update_hsv = None


BLACK_HSV = {"black": ((0, 0, 0), (180, 255, 80))}
BLUE_HSV = {"blue": ((92, 100, 62), (121, 255, 255))}


class HSVConfigTwoTest(unittest.TestCase):
    def setUp(self):
        if update_hsv is None:
            self.skipTest("cv2 is only available on the robot runtime")

    def test_color_detection_treats_camera_frames_as_rgb(self):
        image = np.zeros((240, 320, 3), dtype=np.uint8)
        image[:, :] = [255, 255, 255]
        image[180:235, 130:190] = [0, 0, 255]

        _view, _binary, names, xy = update_hsv().get_contours(image, BLUE_HSV)

        self.assertEqual(names, ["blue"])
        self.assertGreaterEqual(xy[0][0], 130)
        self.assertLessEqual(xy[0][0], 190)

    def test_black_detection_rejects_dark_saturated_color(self):
        image = np.full((240, 320, 3), 255, dtype=np.uint8)
        image[180:235, 130:190] = [10, 70, 10]

        _view, _binary, names, xy = update_hsv().get_contours(image, BLACK_HSV)

        self.assertEqual(names, [])
        self.assertEqual(xy, [])

    def test_black_contour_prefers_closest_to_bottom_over_largest_area(self):
        image = np.full((240, 320, 3), 255, dtype=np.uint8)
        image[20:120, 10:310] = 0
        image[210:235, 130:190] = 0

        _view, _binary, names, xy = update_hsv().get_contours(image, BLACK_HSV)

        self.assertEqual(names, ["black"])
        self.assertGreaterEqual(xy[0][1], 200)
        self.assertGreaterEqual(xy[0][0], 130)
        self.assertLessEqual(xy[0][0], 190)

    def test_debug_overlay_uses_one_rgb_marker_color(self):
        image = np.full((240, 320, 3), 255, dtype=np.uint8)
        image[180:235, 130:190] = 0

        view, _binary, names, _xy = update_hsv().get_contours(image, BLACK_HSV)

        self.assertEqual(names, ["black"])
        yellow_pixels = np.all(view == [255, 255, 0], axis=2)
        red_pixels = np.all(view == [255, 0, 0], axis=2)
        blue_pixels = np.all(view == [0, 0, 255], axis=2)
        self.assertTrue(yellow_pixels.any())
        self.assertFalse(red_pixels.any())
        self.assertFalse(blue_pixels.any())


if __name__ == "__main__":
    unittest.main()
