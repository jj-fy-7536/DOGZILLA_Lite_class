import cv2
import numpy as np
import math
import os,sys,time,json,base64
import spidev as SPI
from PIL import Image,ImageDraw,ImageFont
import xgoscreen.LCD_2inch as LCD_2inch
import RPi.GPIO as GPIO
import subprocess
from picamera2 import Picamera2

GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)

#人脸检测
def getFaceBox(net, frame, conf_threshold=0.7):
        frameOpencvDnn = frame.copy()
        frameHeight = frameOpencvDnn.shape[0]
        frameWidth = frameOpencvDnn.shape[1]
        blob = cv2.dnn.blobFromImage(frameOpencvDnn, 1.0, (300, 300), [104, 117, 123], True, False)
        net.setInput(blob)
        detections = net.forward()
        bboxes = []
        for i in range(detections.shape[2]):
            confidence = detections[0, 0, i, 2]
            if confidence > conf_threshold:
                x1 = int(detections[0, 0, i, 3] * frameWidth)
                y1 = int(detections[0, 0, i, 4] * frameHeight)
                x2 = int(detections[0, 0, i, 5] * frameWidth)
                y2 = int(detections[0, 0, i, 6] * frameHeight)
                bboxes.append([x1, y1, x2, y2])
                cv2.rectangle(frameOpencvDnn, (x1, y1), (x2, y2), (0, 255, 0), int(round(frameHeight / 150)),8)  
        return frameOpencvDnn, bboxes


class Gender_And_Age():
    def __init__(self):
        self.display = LCD_2inch.LCD_2inch()
        self.display.Init()
        self.display.clear()
        self.splash = Image.new("RGB",(320,240),"black")
        self.display.ShowImage(self.splash)
        self.picam2=None
        self.agesexmark=None
        self.keys = {
            "A": 24,
            "B": 23,
            "C": 17,
            "D": 22
        }
        self.setup_pins()

        self.open_camera()
    #key_value
    '''
    a左上按键
    b右上按键
    c左下按键
    d右下按键
    返回值 0未按下,1按下
    '''
    def setup_pins(self):
      
        for pin in self.keys.values():
            os.system(f"sudo pinctrl set {pin} ip")

    def read_pin(self, pin):
      
        result = subprocess.run(["sudo", "pinctrl", "level", str(pin)], capture_output=True, text=True).stdout
        return result[0] == "1"

    def press_button(self, key_name):
        
        pin = self.keys.get(key_name)
        if pin is None:
            return False
        
        if self.read_pin(pin):
            return False

        # Wait until the button is released (pin reads '1')
        while not self.read_pin(pin):
            time.sleep(0.01)
        return True

    def press_a(self):
        return self.press_button("A")

    def press_b(self):
        if self.press_button("B"):
            os.system("pkill mplayer")
            return True
        return False

    def press_c(self):
        return self.press_button("C")

    def press_d(self):
        return self.press_button("D")
        
    def open_camera(self):
        if self.picam2==None:
            self.picam2 = Picamera2()
            self.picam2.configure(
                self.picam2.create_preview_configuration(main={"format": "RGB888", "size": (320, 240)})
            )
            self.picam2.start()
    def close_camera(self):
        self.picam2.stop()
        self.picam2.close()

    

    '''
    年纪及性别检测
    '''
    def agesex(self,target="camera"):
        ret=''
        MODEL_MEAN_VALUES = (78.4263377603, 87.7689143744, 114.895847746)
        ageList = ['(0-2)', '(4-6)', '(8-12)', '(15-20)', '(25-32)', '(38-43)', '(48-53)', '(60-100)']
        genderList = ['Male', 'Female']
        padding = 20
        if target=="camera":
            #self.open_camera()
            #success,image = self.cap.read()
            frame = self.picam2.capture_array() 
            image = cv2.flip(frame, 1)
        else:
            image=np.array(Image.open(target))
        if self.agesexmark==None:
            faceProto = "/home/pi/model/opencv_face_detector.pbtxt"
            faceModel = "/home/pi/model/opencv_face_detector_uint8.pb"
            ageProto = "/home/pi/model/age_deploy.prototxt"
            ageModel = "/home/pi/model/age_net.caffemodel"
            genderProto = "/home/pi/model/gender_deploy.prototxt"
            genderModel = "/home/pi/model/gender_net.caffemodel"
            self.ageNet = cv2.dnn.readNet(ageModel, ageProto)
            self.genderNet = cv2.dnn.readNet(genderModel, genderProto)
            self.faceNet = cv2.dnn.readNet(faceModel, faceProto)
            self.agesexmark=True

        image = cv2.flip(image, 1)
        frameFace, bboxes = getFaceBox(self.faceNet, image)
        gender=''
        age=''
        for bbox in bboxes:
            face = image[max(0, bbox[1] - padding):min(bbox[3] + padding, image.shape[0] - 1),
                    max(0, bbox[0] - padding):min(bbox[2] + padding, image.shape[1] - 1)]
            blob = cv2.dnn.blobFromImage(face, 1.0, (227, 227), MODEL_MEAN_VALUES, swapRB=False)
            self.genderNet.setInput(blob)   
            genderPreds = self.genderNet.forward()   
            gender = genderList[genderPreds[0].argmax()]  
            self.ageNet.setInput(blob)
            agePreds = self.ageNet.forward()
            age = ageList[agePreds[0].argmax()]
            label = "{},{}".format(gender, age)
            cv2.putText(frameFace, label, (bbox[0], bbox[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2,cv2.LINE_AA)  
            ret=(gender,age,(bbox[0], bbox[1]))

        #显示到电脑上
        cv2.imshow('frame', frameFace)
        cv2.waitKey(1)

        b,g,r = cv2.split(frameFace)
        frameFace = cv2.merge((r,g,b))
        imgok = Image.fromarray(frameFace)
        self.display.ShowImage(imgok)
        if ret=='':
            return None
        else:
            return ret

    

myedu = Gender_And_Age()

try:
    while True:
        result = myedu.agesex()
        print(result)
        if myedu.press_b():   #b键按下退出循环
            myedu.display.clear()
            myedu.splash = Image.new("RGB",(320,240),"black")
            myedu.display.ShowImage(myedu.splash)
            break      
except:
    myedu.close_camera()
    myedu.display.clear()
    myedu.splash = Image.new("RGB",(320,240),"black")
    myedu.display.ShowImage(myedu.splash)
    del myedu