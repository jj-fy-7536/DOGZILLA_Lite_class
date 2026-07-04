import xgoscreen.LCD_2inch as LCD_2inch
from PIL import Image,ImageDraw,ImageFont
from xgolib import XGO
import cv2
import threading
from gesture_action import handDetector  
from key import Button
from picamera2 import Picamera2

# 线程功能操作库 Thread function operation library
import inspect
import ctypes
def _async_raise(tid, exctype):
    """raises the exception, performs cleanup if needed"""
    tid = ctypes.c_long(tid)
    if not inspect.isclass(exctype):
        exctype = type(exctype)
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, ctypes.py_object(exctype))
    if res == 0:
        raise ValueError("invalid thread id")
    elif res != 1:
        # """if it returns a number greater than one, you're in trouble,
        # and you should call it again with exc=NULL to revert the effect"""
        ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, None)
        
def stop_thread(thread):
    _async_raise(thread.ident, SystemExit)

hand_detector = handDetector(detectorCon=0.8)
picam2 = Picamera2()
picam2.configure(
    picam2.create_preview_configuration(main={"format": "RGB888", "size": (320, 240)})
)
picam2.start()


g_dog = XGO(port='/dev/ttyAMA0',version="xgolite")



mydisplay = LCD_2inch.LCD_2inch()
mydisplay.clear()
splash = Image.new("RGB", (mydisplay.height, mydisplay.width ),"black")   
mydisplay.ShowImage(splash)
button = Button()



try:
    while True:
        global bot
        frame = picam2.capture_array() 
        
        img_height, img_width, _ = frame.shape
        hand_detector.findHands(frame, draw=False) 
        if len(hand_detector.lmList) != 0:
            # 转向控制部分
            # Turning control section
            # MediaPipe中, 手部最中心的指关节的编号为9
            # In MediaPipe, the index of the central finger joint is 9
            x,y = hand_detector.findPoint(9)
            cv2.circle(frame,(int(x),int(y)),2,(0,255,255),6)

            value_x = x - 160
            value_y = y - 120

            if value_x > 55:
                value_x = 55
            elif value_x < -55:
                value_x = -55
            if value_y > 75:
                value_y = 75
            elif value_y < -75:
                value_y = -75

            finger_number = hand_detector.get_gesture()
            finger_str=f"Number:{finger_number}"

            #print(finger_number)

            if(finger_number != "Zero"):
                g_dog.attitude(['y','p'],[-value_x/10, value_y/10])
           

        else:
            x = 0
            y = 0

        try:
            cv2.imshow('frame',frame)
            cv2.waitKey(1)

            #图片显示在lcd屏上
            b,g,r = cv2.split(frame)
            frame = cv2.merge((r,g,b))
            frame = cv2.flip(frame, 1)
            imgok = Image.fromarray(frame)
            mydisplay.ShowImage(imgok)  
        except:
            continue

        if button.press_b():
            break
finally:
    g_dog.reset()
    picam2.stop()
    picam2.close()
    
