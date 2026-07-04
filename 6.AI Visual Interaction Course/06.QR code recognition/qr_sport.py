import cv2
import os,socket,sys,time
import spidev as SPI
import xgoscreen.LCD_2inch as LCD_2inch
from PIL import Image,ImageDraw,ImageFont
from key import Button
import numpy as np
from xgolib import XGO 
import threading
import inspect
import ctypes
from picamera2 import Picamera2



display = LCD_2inch.LCD_2inch()
display.clear()
splash = Image.new("RGB", (display.height, display.width ),"black")
display.ShowImage(splash)
button=Button()

g_dog = XGO("xgolite")
g_barcodeData = ''
g_Tstop = True

# 关闭线程  stop thread
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



#-----------------------COMMON INIT-----------------------
import pyzbar.pyzbar as pyzbar

def cv2AddChineseText(img, text, position, textColor=(0, 255, 0), textSize=30):
    if (isinstance(img, np.ndarray)):  
        img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img)
    fontStyle = ImageFont.truetype(
        "/home/pi/model/msyh.ttc", textSize, encoding="utf-8")
    draw.text(position, text, textColor, font=fontStyle)
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)

 
font = cv2.FONT_HERSHEY_SIMPLEX 
picam2 = Picamera2()
picam2.configure(
    picam2.create_preview_configuration(main={"format": "RGB888", "size": (320, 240)})
)
picam2.start()

def control_action():
    global g_barcodeData,g_Tstop
    while g_Tstop:
        time.sleep(1)
        barcodeData = g_barcodeData
        #print('control_action',barcodeData)
        if(barcodeData == "goahead"):
            g_dog.move('x',20) 
            time.sleep(2)
            g_dog.stop()

        elif(barcodeData == "goback"):
            g_dog.move('x',-20) 
            time.sleep(2)
            g_dog.stop()

        elif(barcodeData == "turnright"):
            g_dog.turn(-20) 
            time.sleep(2)
            g_dog.stop()
            
        elif(barcodeData == "turnleft"):
            g_dog.turn(20) 
            time.sleep(2)
            g_dog.stop()

        elif(barcodeData == "updown"):
            g_dog.action(6) #蹲起 updown

        elif(barcodeData == "shake"):
            g_dog.action(10) 

        elif(barcodeData == "armup"):
            g_dog.action(128)
        elif(barcodeData == "armmiddle"):
            g_dog.action(129)
        elif(barcodeData == "armdown"):
            g_dog.action(130)

        g_barcodeData = ''



# 解析图像中的二维码信息  Analyze the qrcode information in the image
def decodeDisplay(image, qrdisplay):
    global g_barcodeData
    barcodes = pyzbar.decode(image)
    for barcode in barcodes:
        # 提取二维码的边界框的位置, 画出图像中条形码的边界框
        # Extract the position of the bounding box of the qrcode, 
        # and draw the bounding box of the barcode in the image
        (x, y, w, h) = barcode.rect
        cv2.rectangle(qrdisplay, (x, y), (x + w, y + h), (225, 225, 225), 2)

        # 提取二维码数据为字节对象，转换成字符串
        # The qrcode data is extracted as byte objects and converted into strings
        g_barcodeData = barcode.data.decode("utf-8")
        barcodeType = barcode.type

        # 绘出图像上条形码的数据和条形码类型  
        # Plot the barcode data and barcode type on the image
        text = "{} ({})".format(g_barcodeData, barcodeType)
        cv2.putText(qrdisplay, text, (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (225, 0, 0), 2)

        print("[INFO] Found {} barcode: {}".format(barcodeType, g_barcodeData))
        
    return qrdisplay


# 检测二维码  detect qrcode
def Detect_Qrcode_Task():
    t_start = time.time()
    fps = 0
    while True:
        frame = picam2.capture_array() 
        frame = cv2.flip(frame, 1)

        # 转为灰度图像  Convert to grayscale image
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        frame = decodeDisplay(gray, frame)
        fps = fps + 1
        mfps = fps / (time.time() - t_start)
        cv2.putText(frame, "FPS " + str(int(mfps)), (40,40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,255), 3)
        
        cv2.imshow("image1",frame)

        b,g,r = cv2.split(frame)
        img = cv2.merge((r,g,b))
        imgok = Image.fromarray(img)
        display.ShowImage(imgok)
        if (cv2.waitKey(1)) == ord('q'):
            break
        if button.press_b():
            break


try:
    thread1 = threading.Thread(target=control_action)
    thread1.setDaemon(True)
    thread1.start()
    Detect_Qrcode_Task()
except:
    g_Tstop = False
    g_dog.reset()
    picam2.stop()
    picam2.close()


        


    
        


    

