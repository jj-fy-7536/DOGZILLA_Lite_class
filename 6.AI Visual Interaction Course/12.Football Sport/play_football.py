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

#初始化pid init pid
Px = 0.25 #0.35
Ix = 0
Dx = 0.0001
X_Middle_error = 160 #图像X轴中心 Image X-axis center
X_track_PID = PID.PositionalPID(Px, Ix, Dx) 
obj_error = 100 #球距离中心点的偏差 Deviation of the ball from the center point

four_leg = [11,12,13,21,22,23,31,32,33,41,42,43]

g_dog = XGO(port='/dev/ttyAMA0',version="xgolite")

#让狗蹲下，重心后移 Let the dog squat down and shift its center of gravity backwards
g_dog.attitude(['p'],[15])
time.sleep(0.2)
#蹲下找寻
g_dog.translation(['z'],[75])
time.sleep(0.2)


g_dog.pace('slow') #巡线的速度 Speed of patrol line
time.sleep(.2)



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
 
mode=4 
color_lower = np.array([26, 100, 91])
color_upper = np.array([32, 255, 255])

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
        color_lower = np.array([0, 43, 46])
        color_upper = np.array([10, 255, 255])
    elif mode==2: #green
        color_lower = np.array([35, 43, 46])
        color_upper = np.array([77, 255, 255])
    elif mode==3:   #blue
        color_lower = np.array([100, 43, 46])
        color_upper = np.array([124, 255, 255])
    elif mode==4:   #yellow
        color_lower = np.array([26, 100, 91])
        color_upper = np.array([32, 255, 255])
        # color_lower = np.array([26, 43, 46])
        # color_upper = np.array([34, 255, 255])


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
                   
                    X_track_PID.SystemOutput = color_x - obj_error #X 因为球要在右足方向上  Because the ball needs to be in the right foot direction
                    X_track_PID.SetStepSignal(X_Middle_error)
                    X_track_PID.SetInertiaTime(0.01, 0.1)               
                    x_real_value = int(X_track_PID.SystemOutput)
                    x_real_value = limit_fun(x_real_value ,-18,18)
                    
                    g_dog.move('y',x_real_value)

                    if color_y > 225 or color_y ==0 :
                        g_dog.move('x',0) 
                    else :
                        g_dog.move('x',10) 

                #停止定点，进行踢球运动 Stop stationary and start playing soccer
                if color_y > 225 :
                    if abs(color_x -obj_error -160)<15:###6
                        step = step+1
                    else :
                        step = 0
                    if step > 5:
                        g_dog.stop()
                        g_mode = 2 #进入踢球运动 Enter the sport of football
            else:
                color_x = 0
                color_y = 0
                g_dog.stop()
            
            #print([color_x,color_y])
            cv2.putText(frame, "X:%d, Y%d" % (int(color_x), int(color_y)), (40,40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,255), 3)
            t_start = time.time()
            fps = 0

        elif g_mode == 2: #踢球运动  Football sports
            
            g_dog.reset()#恢复动作
            time.sleep(0.2)
            g_dog.translation(['x'],[-10])
            time.sleep(0.5)
            g_dog.attitude(['p'],[0]) 
            time.sleep(0.5)

            g_dog.motor_speed(200) #加快运动速度 Accelerate the speed of movement
            time.sleep(0.2)
            g_dog.motor(four_leg,[9.06, 29.14, -1.29, -15.88, 41.69, -16.35, 3.88, 41.06, -0.12, 4.35, 40.43, -0.35])#收
            time.sleep(0.2)
            g_dog.motor(four_leg,[9.06, 29.14, -1.29, 4.35, 6.55, -16.12, 3.88, 41.06, -0.35, 4.35, 40.43, 0.12])#伸
            time.sleep(0.2)
            g_dog.motor(four_leg,[8.59, 28.51, -0.59, 50.0, -44.9, -16.59, 3.88, 41.06, -0.12, 4.35, 40.43, 0.12])#踢
            time.sleep(0.2)
            g_dog.motor(four_leg,[8.59, 28.51, -0.35, 8.12, 28.51, 0.12, 3.88, 41.06, -0.12, 4.35, 40.43, -0.59])#收回
            time.sleep(1)
            
            g_dog.translation(['x'],[0])#恢复追踪目标的姿态 Restore the posture of the tracked target
            time.sleep(0.2)
            g_dog.attitude(['p'],[15])
            time.sleep(0.2)
            g_dog.translation(['z'],[75])
            time.sleep(0.2)

            g_dog.pace('slow') #恢复寻找目标的速度 Restore the speed of searching for targets
            time.sleep(0.2)

            step = 0 
            g_mode = 1 

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
    g_dog.stop()
    picam2.stop()
    picam2.close()
    cv2.destroyAllWindows() 
