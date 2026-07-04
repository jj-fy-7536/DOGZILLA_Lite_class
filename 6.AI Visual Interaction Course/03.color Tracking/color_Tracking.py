import cv2
import os,socket,sys,time
import spidev as SPI
import xgoscreen.LCD_2inch as LCD_2inch
from PIL import Image,ImageDraw,ImageFont
from key import Button
import numpy as np
import mediapipe as mp
from numpy import linalg
from xgolib import XGO
import PID
from picamera2 import Picamera2


#初始化pid PID init
Px = 0.0688
Ix = 0
Dx = 0.000001
X_Middle_error = 160 #图像X轴中心 Image X-axis center
X_track_PID = PID.PositionalPID(Px, Ix, Dx) 

Py = 0.07
Iy = 0
Dy = 0.000001
Y_Middle_error = 120 #图像Y轴中心 Image Y-axis center
Y_track_PID = PID.PositionalPID(Py, Iy, Dy) #Y轴 PID参数 Y-axis PID parameters


g_dog = XGO(port='/dev/ttyAMA0',version="xgolite")



red=(255,0,0)
green=(0,255,0)
blue=(0,0,255)
yellow=(255,255,0)
display = LCD_2inch.LCD_2inch()
display.clear()
splash = Image.new("RGB", (display.height, display.width ),"black")   
display.ShowImage(splash)
button=Button()



g_mode=1 
 
mode=1 
color_lower = np.array([0, 70, 72])
color_upper = np.array([7, 255, 255])

def limit_fun(input,min,max):
    if input < min:
        input = min
    elif input > max:
        input = max
    return input

def change_color():
    global color_lower,color_upper,mode
    if mode==4:
        mode=1
    else:
        mode+=1
    if mode==1:  #red
        color_lower = np.array([0, 70, 72])
        color_upper = np.array([7, 255, 255])
    elif mode==2: #green
        color_lower = np.array([35, 43, 46])
        color_upper = np.array([77, 255, 255])
    elif mode==3:   #blue
        color_lower = np.array([92, 100, 62])
        color_upper = np.array([121, 251, 255])
    elif mode==4:   #yellow
        color_lower = np.array([26, 100, 91])
        color_upper = np.array([32, 255, 255])


#-----------------------COMMON INIT-----------------------
font = cv2.FONT_HERSHEY_SIMPLEX 
picam2 = Picamera2()
picam2.configure(
    picam2.create_preview_configuration(main={"format": "RGB888", "size": (320, 240)})
)
picam2.start()

t_start = time.time()
fps = 0
color_x = 0
color_y = 0
color_radius = 0
step = 0 #用于记录到达目标的时间点 Used to record the time point of arrival at the target

try:
    while 1:
        frame = picam2.capture_array() 
        #frame = cv2.flip(frame, 1)

        frame_ = cv2.GaussianBlur(frame,(5,5),0)                    
        hsv = cv2.cvtColor(frame,cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv,color_lower,color_upper)  
        mask = cv2.erode(mask,None,iterations=2)
        mask = cv2.dilate(mask,None,iterations=2)
        mask = cv2.GaussianBlur(mask,(3,3),0)     
        cnts = cv2.findContours(mask.copy(),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[-2] 

        
        if g_mode == 1:
            if len(cnts) > 0:
                cnt = max (cnts, key = cv2.contourArea)
                (color_x,color_y),color_radius = cv2.minEnclosingCircle(cnt)

                if color_radius > 10:
                    cv2.circle(frame,(int(color_x),int(color_y)),int(color_radius),(255,0,255),2)  

                    X_track_PID.SystemOutput = color_x #X 
                    X_track_PID.SetStepSignal(X_Middle_error)
                    X_track_PID.SetInertiaTime(0.01, 0.1)       

                    x_real_value = int(X_track_PID.SystemOutput)
                    x_real_value = limit_fun(x_real_value,-15,15)
    
                    Y_track_PID.SystemOutput = color_y #y 
                    Y_track_PID.SetStepSignal(Y_Middle_error)
                    Y_track_PID.SetInertiaTime(0.01, 0.1)               
                    y_real_value = int(Y_track_PID.SystemOutput)
                    y_real_value = limit_fun(y_real_value,-11,11)
            
                    g_dog.attitude(['p','y'],[-y_real_value,x_real_value])

            else:
                color_x = 0
                color_y = 0
                g_dog.stop()

            cv2.putText(frame, "X:%d, Y%d" % (int(color_x), int(color_y)), (40,40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,255), 3)
            t_start = time.time()
            fps = 0


        else:
            fps = fps + 1
            mfps = fps / (time.time() - t_start)
            cv2.putText(frame, "FPS " + str(int(mfps)), (40,40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,255), 3)

        
        b,g,r = cv2.split(frame)
        img = cv2.merge((r,g,b))
        if mode==1:
            cv2.rectangle(img, (290, 10), (320, 40), red, -1)
        elif mode==2:
            cv2.rectangle(img, (290, 10), (320, 40), green, -1)
        elif mode==3:
            cv2.rectangle(img, (290, 10), (320, 40), blue, -1)
        elif mode==4:
            cv2.rectangle(img, (290, 10), (320, 40), yellow, -1)
        imgok = Image.fromarray(img)
        display.ShowImage(imgok)

        r,g,b = cv2.split(img)
        framecv = cv2.merge((b,g,r))
        #显示到电脑上 Display on computer
        cv2.imshow("frame",framecv)


        if (cv2.waitKey(1)) == ord('q'):
            break
        if button.press_b():
            g_dog.stop()
            break
        if button.press_d():
            change_color()

except:
    g_dog.reset()
    picam2.stop()
    picam2.close()
    cv2.destroyAllWindows() 
