from statistics import mode
from PIL import Image, ImageDraw
import xgoscreen.LCD_2inch as LCD_2inch

import cv2
from keras.models import load_model
import numpy as np

from utils.datasets import get_labels
from utils.inference import detect_faces
from utils.inference import draw_text
from utils.inference import draw_bounding_box
from utils.inference import apply_offsets
from utils.inference import load_detection_model
from utils.preprocessor import preprocess_input


splash_theme_color = (255,255,255)
display = LCD_2inch.LCD_2inch()
display.Init()
display.clear()
# Init Splash
splash = Image.new("RGB", (display.height, display.width), splash_theme_color)
draw = ImageDraw.Draw(splash)
display.ShowImage(splash)

# parameters for loading data and images
detection_model_path = '/home/pi/RaspberryPi-CM5/face_classification-master/trained_models/detection_models/haarcascade_frontalface_default.xml'
emotion_model_path = '/home/pi/RaspberryPi-CM5/face_classification-master/trained_models/emotion_models/fer2013_mini_XCEPTION.102-0.66.hdf5'
emotion_labels = get_labels('fer2013')

# hyper-parameters for bounding boxes shape
frame_window = 10
emotion_offsets = (20, 40)

# loading models
face_detection = load_detection_model(detection_model_path)
emotion_classifier = load_model(emotion_model_path, compile=False)

# getting input model shapes for inference
emotion_target_size = emotion_classifier.input_shape[1:3]

# starting lists for calculating modes
emotion_window = []

# starting video streaming
from key import Button,language
from picamera2 import Picamera2
picam2 = Picamera2()
picam2.configure(picam2.create_preview_configuration(main={"format": 'RGB888', "size": (320, 240)}))
picam2.start()
# print("摄像头初始化完毕")
button = Button()
la = language()



Count = 10
Count_num = 0
last_emotion = None
while True:
    bgr_image = picam2.capture_array()
    gray_image = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2GRAY)
    rgb_image = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2RGB)
    faces = detect_faces(face_detection, gray_image)

    for face_coordinates in faces:

        x1, x2, y1, y2 = apply_offsets(face_coordinates, emotion_offsets)
        gray_face = gray_image[y1:y2, x1:x2]
        try:
            gray_face = cv2.resize(gray_face, (emotion_target_size))
        except:
            continue

        gray_face = preprocess_input(gray_face, True)
        gray_face = np.expand_dims(gray_face, 0)
        gray_face = np.expand_dims(gray_face, -1)
        emotion_prediction = emotion_classifier.predict(gray_face)
        emotion_probability = np.max(emotion_prediction)
        emotion_label_arg = np.argmax(emotion_prediction)
        emotion_text = emotion_labels[emotion_label_arg]
        emotion_window.append(emotion_text)
        if emotion_text != last_emotion:
            Count_num = 0
        else:
            Count_num += 1
        last_emotion = emotion_text

        if len(emotion_window) > frame_window:
            emotion_window.pop(0)
        try:
            emotion_mode = mode(emotion_window)
        except:
            continue

        #print(f"当前的情绪为: {emotion_text},概率为{emotion_probability},连续计数为: {Count_num}")
        if la == "cn":
            print(f"当前的情绪为: {emotion_text},概率为{emotion_probability}")
        else:
            print(f"The current emotion is: {emotion_text},The probability is{emotion_probability}")
            
        if emotion_text == 'angry' and Count_num >= Count :
            color = emotion_probability * np.asarray((255, 0, 0))
            Count_num = 0
        elif emotion_text == 'sad' and Count_num >= Count :
            color = emotion_probability * np.asarray((0, 0, 255))
            Count_num = 0
        elif emotion_text == 'happy' and Count_num >= Count :
            color = emotion_probability * np.asarray((255, 255, 0))
            Count_num = 0
        elif emotion_text == 'surprise' and Count_num >= Count :
            color = emotion_probability * np.asarray((0, 255, 255))
            Count_num = 0
        elif emotion_text == 'neutral' and Count_num >= Count :
            color = emotion_probability * np.asarray((0, 255, 255))
            Count_num = 0
        else:
            color = emotion_probability * np.asarray((0, 255, 0))
            # # 假设其他情绪为恐惧和厌恶 Assuming other emotions are fear and disgust
            # if emotion_text in ['fear', 'scared']:  
            #     show("Stun", 8, "fear")  # 恐惧 fear
            # elif emotion_text in ['disgust', 'repulsed']:  
            #     show("shame", 11, "disgust")  # 厌恶 disgust

        color = color.astype(int)
        color = color.tolist()
        
        draw_bounding_box(face_coordinates, rgb_image, color)
        draw_text(face_coordinates, rgb_image, emotion_mode,
                  color, 0, -45, 1, 1)

    bgr_image = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2BGR)
    b, g, r = cv2.split(bgr_image)
    img = cv2.merge((r, g, b))
    imgok = Image.fromarray(img)
    display.ShowImage(imgok)  
    cv2.imshow("frame",bgr_image)
    
    if button.press_b():
        break
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break
