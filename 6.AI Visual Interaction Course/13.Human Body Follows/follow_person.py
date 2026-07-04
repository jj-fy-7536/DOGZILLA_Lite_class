import cv2
import numpy as np
import onnxruntime
import time
from picamera2 import Picamera2

from key import Button,language
la = language()


# 定义相机分辨率和用于距离计算的已知参数的常量 Define constants for camera resolution and known parameters used for distance calculation
CAMERA_WIDTH = 320
CAMERA_HEIGHT = 240
KNOWN_DISTANCE = 76.2  # 从相机到人脸的示例已知距离 Example of known distance from camera to face
KNOWN_WIDTH = 14.3  # 人脸的示例已知宽度 Example of face with known width
FOCAL_LENGTH = 500  # 相机的预设焦距 The preset focal length of the camera

# 打开摄像头 open camera
picam2 = Picamera2()
picam2.configure(picam2.create_preview_configuration(main={"format": 'RGB888', "size": (320, 240)}))
picam2.start()
#print("摄像头初始化完毕")


class HumanDetector:
    def __init__(self):
        """初始化人体检测器，加载ONNX模型  Initialize the human body detector and load the ONNX model"""
        self.session = onnxruntime.InferenceSession('/home/pi/RaspberryPi-CM5/model/Model.onnx')
        self.prev_time = time.time()
        self.frame_count = 0

    def sigmoid(self, x):
        return 1. / (1 + np.exp(-x))

    def tanh(self, x):
        return 2. / (1 + np.exp(-2 * x)) - 1

    def preprocess(self, src_img, size):
        """预处理图像，调整大小并归一化 Preprocess the image, resize and normalize it"""
        output = cv2.resize(src_img, (size[0], size[1]), interpolation=cv2.INTER_AREA)
        output = output.transpose(2, 0, 1)
        output = output.reshape((1, 3, size[1], size[0])) / 255
        return output.astype('float32')

    def nms(self, dets, thresh=0.45):
        """非极大值抑制，用于消除重叠的检测框 Non maximum suppression, used to eliminate overlapping detection boxes"""
        x1 = dets[:, 0]
        y1 = dets[:, 1]
        x2 = dets[:, 2]
        y2 = dets[:, 3]
        scores = dets[:, 4]
        areas = (x2 - x1 + 1) * (y2 - y1 + 1)
        order = scores.argsort()[::-1]
        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(i)
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            w = np.maximum(0.0, xx2 - xx1 + 1)
            h = np.maximum(0.0, yy2 - yy1 + 1)
            inter = w * h
            ovr = inter / (areas[i] + areas[order[1:]] - inter) # 计算交并比(IOU) Calculate Intersection over Union (IOU)
            inds = np.where(ovr <= thresh)[0] # 找到IOU低于阈值的检测框 Find detection boxes with IOU below the threshold
            order = order[inds + 1] # 更新待处理检测框列表 Update the list of pending detection boxes
        output = []
        for i in keep:
            output.append(dets[i].tolist())
        return output

    def detection(self, session, img, input_width, input_height, thresh):
        """执行目标检测，返回检测到的对象边界框 Perform object detection and return the detected object bounding box"""
        try:
            pred = []
            H, W, _ = img.shape
            data = self.preprocess(img, [input_width, input_height])
            input_name = session.get_inputs()[0].name
            feature_map = session.run([], {input_name: data})[0][0]
            feature_map = feature_map.transpose(1, 2, 0)
            feature_map_height = feature_map.shape[0]
            feature_map_width = feature_map.shape[1]
            for h in range(feature_map_height):
                for w in range(feature_map_width):
                    data = feature_map[h][w]
                    obj_score, cls_score = data[0], data[5:].max()
                    score = (obj_score ** 0.6) * (cls_score ** 0.4)
                    if score > thresh:
                        cls_index = np.argmax(data[5:])
                        x_offset, y_offset = self.tanh(data[1]), self.tanh(data[2])
                        box_width, box_height = self.sigmoid(data[3]), self.sigmoid(data[4])
                        box_cx = (w + x_offset) / feature_map_width
                        box_cy = (h + y_offset) / feature_map_height
                        x1, y1 = box_cx - 0.5 * box_width, box_cy - 0.5 * box_height
                        x2, y2 = box_cx + 0.5 * box_width, box_cy + 0.5 * box_height
                        x1, y1, x2, y2 = int(x1 * W), int(y1 * H), int(x2 * W), int(y2 * H)
                        pred.append([x1, y1, x2, y2, score, cls_index])
            return self.nms(np.array(pred))
        except:
            return None

    def object_data(self, image):
        """获取图像中的对象检测数据 Obtain object detection data in the image"""
        input_width, input_height = 352, 352
        bboxes = self.detection(self.session, image, input_width, input_height, 0.65)
        return bboxes

    
    def distance_finder(self, focal_length, real_width, width_in_rf_image):
        """使用单目视觉原理计算距离 Calculate distance using monocular vision principle"""
        # 距离 = (已知实际宽度 × 焦距) / 图像中的像素宽度  单目相机测距原理 单位cm
        #Distance=(Given actual width x focal length)/Pixel width in image Monocular camera ranging principle Unit: cm
        distance = (real_width * focal_length) / width_in_rf_image
        return distance

    def detect_humans(self, image):
        self.frame_count += 1
        current_time = time.time()
        elapsed_time = current_time - self.prev_time
        if elapsed_time >= 1:
            fps = self.frame_count / elapsed_time
            print(f"推理帧率(FPS): {fps:.2f} FPS")
            self.prev_time = current_time
            self.frame_count = 0

        bboxes = self.object_data(image)
        closest_human = None
        min_distance = float('inf')

        if bboxes:
            for bbox in bboxes:
                if int(bbox[5]) == 0:  # 检查检测到的类别是否是人 Check if the detected category is human
                    x1, y1, x2, y2 = bbox[:4]
                    xx1, yy1, xx2, yy2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
                    object_width_in_frame = x2 - x1
                    object_center_x = (x1 + x2) / 2
                    distance = self.distance_finder(FOCAL_LENGTH, KNOWN_WIDTH, object_width_in_frame)

                    if distance < min_distance:
                        min_distance = distance
                        closest_human = {
                            'bbox': (xx1, yy1, xx2, yy2),
                            'center_x': object_center_x,
                            'distance': distance
                        }

        if closest_human:
            xx1, yy1, xx2, yy2 = closest_human['bbox']
            cv2.rectangle(image, (xx1, yy1), (xx2, yy2), (255, 255, 0), 2)

        return [closest_human] if closest_human else []

from track import HumanTracker
from xgolib import XGO

button = Button()
dog = XGO(port="/dev/ttyAMA0", version="xgolite")
dog.attitude('p', -10)

if __name__ == "__main__":
    tracker = HumanTracker()
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
    detector = HumanDetector()
    last_move_x_speed = 0.0
    last_turn_speed = 0.0
    filter_coefficient = 0.3  # 滤波系数 filter coefficient
    while True:
        frame = picam2.capture_array()
        humans = detector.detect_humans(frame)
        for human in humans:
            if human:
                print("\n=== update  ===")
                center_x, distance = human['center_x'], human['distance'] if human else (None, None)
                #print(f"距离最近的人在 ({human['bbox']}), center_x: {human['center_x']}, distance: {human['distance']} cm")
                tracking_params = tracker.update(human)
                if tracking_params['is_detected'] and tracking_params['is_moving']:
                    prediction = tracker.predict_future_position(tracking_params, 0.06)
                    if prediction:
                        pass
                raw_move_x_speed = tracking_params['velocity_x'] * 0.05 + (distance - 100) * 0.1
                if -20 <distance - 70 <  20:
                    raw_move_x_speed = (distance - 70) * 0.3
                else: 
                    raw_move_x_speed = (distance - 70) * 0.2
                raw_turn_speed = tracking_params['velocity_x'] * 0.02
                if 145<center_x<175:
                    raw_turn_speed = 0
                elif 130<center_x<145 or 175< center_x <190:
                    raw_turn_speed = (160 - center_x) * 0.2
                else:
                    raw_turn_speed = (160 - center_x) * 0.15
                move_x_speed = filter_coefficient * last_move_x_speed + (1 - filter_coefficient) * raw_move_x_speed
                turn_speed = filter_coefficient * last_turn_speed + (1 - filter_coefficient) * raw_turn_speed
                max_move_speed = 10.0
                max_turn_speed = 15.0
                move_x_speed = max(-max_move_speed, min(max_move_speed, move_x_speed))
                turn_speed = max(-max_turn_speed, min(max_turn_speed, turn_speed))
                # 更新上一时刻的速度 Update the speed from the previous moment
                last_move_x_speed = move_x_speed
                last_turn_speed = turn_speed
                if la =="cn":
                    print(f'距离:{distance}, 中心点偏移：{160-center_x}')
                    print(f"移动速度: {move_x_speed:.2f}, 转向速度: {turn_speed:.2f}")
                else:
                    print(f'distance:{distance}, center_x：{160-center_x}')
                    print(f"move_x_speed: {move_x_speed:.2f}, turn_speed: {turn_speed:.2f}")
            
                dog.move_x(move_x_speed)
                dog.turn(turn_speed)
                # #dog.move_x(0.02 * tracking_params['velocity_y'])
                # #dog.turn(tracking_params['velocity_x'])
                
                
        # 如果没有检测到人体，逐渐减速 If no human body is detected, gradually slow down
        if humans == []:
            move_x_speed = 0.03*filter_coefficient * last_move_x_speed 
            turn_speed = 0.03*filter_coefficient * last_turn_speed 
            max_move_speed = 3.0
            max_turn_speed = 3.0
            move_x_speed = max(-max_move_speed, min(max_move_speed, move_x_speed))
            turn_speed = max(-max_turn_speed, min(max_turn_speed, turn_speed))
            dog.move_x(move_x_speed)
            dog.turn(turn_speed)
            
            if la =="cn":
                print(f"移动速度: {move_x_speed:.2f}, 转向速度: {turn_speed:.2f}")
            else:
                print(f"move_x_speed: {move_x_speed:.2f}, turn_speed: {turn_speed:.2f}")
                
            last_move_x_speed = move_x_speed
            last_turn_speed = turn_speed
            
        b, g, r = cv2.split(frame)
        cv2.imshow('Human Detection', frame)
        
        img = cv2.merge((r, g, b))
        imgok = Image.fromarray(img)
        display.ShowImage(imgok)


        
        

        if button.press_b():
            dog.reset()
            break

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break 