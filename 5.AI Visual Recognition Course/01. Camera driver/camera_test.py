import cv2
from picamera2 import Picamera2

try:
  # For webcam input:
  picam2 = Picamera2()
  picam2.configure(
      picam2.create_preview_configuration(main={"format": "RGB888", "size": (320, 240)})
  )
  picam2.start()

  while True:
    frame = picam2.capture_array() 
    frame = cv2.flip(frame, 1)
    
    cv2.imshow("image1",frame)

    if cv2.waitKey(5) & 0xFF == 27:
      break
except:
  picam2.stop()
  picam2.close()