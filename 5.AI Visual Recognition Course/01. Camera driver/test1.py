# -*- coding: utf-8 -*-

import cv2
import time
from picamera2 import Picamera2

picam2 = Picamera2()
picam2.configure(picam2.create_preview_configuration(main={"format": "RGB888", "size": (320, 240)}))
picam2.start()

prev_time = time.time()
frame_count = 0
fps_update_interval = 1.0

while True:
    frame = picam2.capture_array()
    frame = cv2.flip(frame, 1)
    frame_count += 1
    current_time = time.time()
    elapsed = current_time - prev_time
    #if elapsed >= fps_update_interval:
    fps = 1.0 / elapsed
    print(f"FPS: {fps:.2f}")
    frame_count = 0
    prev_time = current_time
    cv2.imshow("image1", frame)
    if cv2.waitKey(5) & 0xFF == 27:
        break

picam2.stop()
picam2.close()
cv2.destroyAllWindows()
