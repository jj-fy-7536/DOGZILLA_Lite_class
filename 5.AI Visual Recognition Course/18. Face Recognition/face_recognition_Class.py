# coding=utf-8
# 人脸识别类 - 使用face_recognition模块
import cv2
import face_recognition
import os
from key import Button,language
from PIL import Image, ImageDraw
import xgoscreen.LCD_2inch as LCD_2inch
splash_theme_color = (255,255,255)
display = LCD_2inch.LCD_2inch()
display.Init()
display.clear()
# Init Splash
splash = Image.new("RGB", (display.height, display.width), splash_theme_color)
draw = ImageDraw.Draw(splash)
display.ShowImage(splash)
button = Button()

la = language()

folder_path = './Face_P/'
if not os.path.exists(folder_path):
    os.makedirs(folder_path)


from picamera2 import Picamera2
picam2 = Picamera2()
picam2.configure(picam2.create_preview_configuration(main={"format": 'RGB888', "size": (320, 240)}))
picam2.start()




## 先拍照，再识别
import time  # 需要导入time模块用于计时
def take_photo():
    global i 
    show_ok = False  # 控制是否显示OK字样
    ok_display_time = 0  # 记录显示OK的时间
    
    while True:
        frame = picam2.capture_array()
        
        # 如果需要显示OK，在画面上添加文字
        if show_ok:
            current_time = time.time()
            # 显示OK字样1秒钟
            if current_time - ok_display_time < 1.0:  # 1秒内显示OK
                # 在画面中央添加OK文字
                font = cv2.FONT_HERSHEY_SIMPLEX
                text = "OK"
                text_size = cv2.getTextSize(text, font, 2, 3)[0]
                text_x = (frame.shape[1] - text_size[0]) // 2
                cv2.putText(frame, text, (text_x, 50), font, 2, (0, 255, 0), 3, cv2.LINE_AA)
            else:
                show_ok = False  # 1秒后停止显示
        
        cv2.imshow('frame', frame)
        
        b, g, r = cv2.split(frame)
        img = cv2.merge((r, g, b))
        imgok = Image.fromarray(img)
        display.ShowImage(imgok) 
        
        if button.press_d():
            cv2.imwrite(f"./Face_P/{i}.jpg", frame)
            i += 1
            # 设置显示OK标志和时间
            show_ok = True
            ok_display_time = time.time()
            
            if la == "cn":
                print("拍照成功")
            else:
                print("Photo taken successfully")
                
        if button.press_c():
            if la=="cn":
                print("退出人脸录入模式，开始识别")
            else:
                print("Exit face input mode and start recognition")
            break
            
        if button.press_b() or (cv2.waitKey(1) & 0xFF == ord('q')):
            exit()
        
def load_photo(i):
    total_image_name = []
    total_face_encoding = []
    for j in range(1, i): 
        fn = f"./Face_P/{j}.jpg"  # 这里是拍照后保存的图片名 Here is the name of the picture saved after taking a photo
        print(fn)
        # try:
        total_face_encoding.append(face_recognition.face_encodings(face_recognition.load_image_file(fn))[0])
        # except:
        #     print(f"人脸识别失败，图片{fn}中人脸识别不清晰或不存在")
        #     print(f"Facial recognition failed, the facial recognition in image {fn} is unclear or non-existent")
        fn = fn[:(len(fn) - 4)]  #截取图片名 Extract the image name 
        total_image_name.append(fn)  #图片名字列表 List of Image Names
    return total_image_name, total_face_encoding

face = False
while (1):
    while True:
        if face == True:
            break
        if la == "cn":
            print("开始人脸录入")
        else:
            print("Start facial recognition")
        i = 1
        take_photo()
        try:
            total_image_name, total_face_encoding = load_photo(i)
            face = True
            print("Face input successful")
            break
        except Exception as e:
            print("Face input Fail")
            continue
    frame = picam2.capture_array()
   
    face_locations = face_recognition.face_locations(frame)
    face_encodings = face_recognition.face_encodings(frame, face_locations)
    # 在这个视频帧中循环遍历每个人脸 Loop through each face in this video frame
    for (top, right, bottom, left), face_encoding in zip(
            face_locations, face_encodings):
        # 看看面部是否与已知人脸相匹配。 Check if the face matches a known face.
        for i, v in enumerate(total_face_encoding):
            match = face_recognition.compare_faces(
                [v], face_encoding, tolerance=0.5)
            name = "Unknown"
            if match[0]:
                total_image_name[i] = total_image_name[i].replace(folder_path,"")
                name = total_image_name[i] #str(i+1) 
                name = 'NO.' + name
                break
        # 画出一个框，框住脸 Draw a frame to enclose the face
        cv2.rectangle(frame, (left, top), (right, bottom), (0, 0, 255), 2)
        # 画出一个带名字的标签，放在框下 Draw a label with a name and place it under the box
        cv2.rectangle(frame, (left, bottom - 35), (right, bottom), (0, 0, 255),
                      cv2.FILLED)
        font = cv2.FONT_HERSHEY_DUPLEX
        try:
            cv2.putText(frame, name, (left + 6, bottom - 6), font, 0.7,
                    (255, 255, 255), 1)
        except :
            name = "Unknown"
            cv2.putText(frame, name, (left + 6, bottom - 6), font, 0.7,
                    (255, 255, 255), 1)
    # 显示结果图像 Display result image
    b, g, r = cv2.split(frame)
    img = cv2.merge((r, g, b))
    imgok = Image.fromarray(img)
    display.ShowImage(imgok)  
    cv2.imshow('frame', frame)
    if button.press_b():
        break
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break
