# import opencv
import cv2
# import hyperlpr3
import hyperlpr3 as lpr3
from picamera2 import Picamera2

# Instantiate object
catcher = lpr3.LicensePlateCatcher()
# load image
#image = cv2.imread("沪AE97033.png")
# print result
# print(catcher(image))


try:
    
    picam2 = Picamera2()
    picam2.configure(
        picam2.create_preview_configuration(main={"format": "RGB888", "size": (320, 240)})
    )
    picam2.start()

    while True:
        frame = picam2.capture_array() 
        #frame = cv2.flip(frame, 1)

        cv2.imshow('frame', frame)
        cher=catcher(frame)
        if(cher):
            print(cher)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

except:
    picam2.stop()
    picam2.close()
