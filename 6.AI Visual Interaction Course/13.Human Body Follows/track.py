import time
import math
import logging

class HumanTracker:
    """人体追踪器，根据人体位置信息计算追踪运动参数
    Human tracker, calculates tracking motion parameters based on human position information"""
    
    def __init__(self, camera_width=320, camera_height=240, 
                 center_threshold=0.1, distance_threshold=100):
        """
        初始化人体追踪器
        Initialize human tracker
        
        参数/Parameters:
            camera_width: 相机画面宽度/Camera frame width
            camera_height: 相机画面高度/Camera frame height
            center_threshold: 画面中心区域阈值比例/Center region threshold ratio
            distance_threshold: 距离变化阈值(厘米)/Distance change threshold (cm)
        """
        self.camera_width = camera_width
        self.camera_height = camera_height
        self.center_threshold = center_threshold
        self.distance_threshold = distance_threshold
        self.last_center_x = None  # 上一次的中心x坐标/Last center x coordinate
        self.last_distance = None  # 上一次的距离/Last distance
        self.last_update_time = time.time()  # 上一次更新时间/Last update time
        
        # 配置日志/Configure logging
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger('HumanTracker')
        
    def update(self, human):
        """
        更新人体位置信息并计算追踪参数
        Update human position information and calculate tracking parameters
        
        参数/Parameters:
            human: 包含人体信息的字典，应包含'center_x'和'distance'键
                   Dictionary containing human information, should include 'center_x' and 'distance' keys
            
        返回/Returns:
            包含追踪参数的字典/Dictionary containing tracking parameters
        """
        # 修正：先判断human是否为None/Fix: First check if human is None
        if human is None:
            center_x, distance = None, None
        else:
            center_x, distance = human['center_x'], human['distance']
        
        # 记录当前时间/Record current time
        current_time = time.time()
        time_elapsed = current_time - self.last_update_time
        
        # 初始化追踪参数/Initialize tracking parameters
        tracking_params = {
            'center_x': center_x,
            'distance': distance,
            'is_detected': human is not None,  # 是否检测到人体/Whether human is detected
            'horizontal_direction': 0,  # -1:左, 0:中, 1:右/-1: left, 0: center, 1: right
            'distance_change': 0,  # 距离变化/Distance change
            'velocity': 0,  # 速度/Velocity
            'velocity_x': 0,  # 水平速度(左右)/Horizontal velocity (left-right)
            'velocity_y': 0,  # 垂直速度(前后)/Vertical velocity (front-back)
            'is_moving': False,  # 是否在移动/Whether moving
            'movement_direction': 0  # 运动方向角度(弧度)/Movement direction angle (radians)
        }
        
        if human:
            # 计算水平方向偏移/Calculate horizontal direction offset
            normalized_center = center_x / self.camera_width - 0.5
            if normalized_center < -self.center_threshold:
                tracking_params['horizontal_direction'] = -1  # 向左/Left
            elif normalized_center > self.center_threshold:
                tracking_params['horizontal_direction'] = 1   # 向右/Right
                
            # 如果有历史数据，计算变化量/If there is historical data, calculate changes
            if self.last_center_x is not None and self.last_distance is not None:
                # 计算距离变化(前后方向)/Calculate distance change (front-back direction)
                tracking_params['distance_change'] = distance - self.last_distance
                
                # 计算水平位置变化(左右方向)/Calculate horizontal position change (left-right direction)
                # 将像素变化转换为实际距离变化，这里使用简化模型
                # Convert pixel change to actual distance change, using a simplified model
                # 假设在距离为1米时，100像素对应10厘米的实际距离
                # Assuming at 1 meter distance, 100 pixels correspond to 10 cm actual distance
                pixel_to_cm = 50.0 / 100  
                center_change = (center_x - self.last_center_x) * pixel_to_cm * (distance / 100)
                print(f'center_change: {center_change:.2f} cm')
                
                # 计算速度 (厘米/秒)/Calculate velocity (cm/s)
                if time_elapsed > 0:
                    # 水平速度(左右)/Horizontal velocity (left-right)
                    tracking_params['velocity_x'] = center_change / time_elapsed
                    # 垂直速度(前后)/Vertical velocity (front-back)
                    tracking_params['velocity_y'] = tracking_params['distance_change'] / time_elapsed
                    # 合速度/Resultant velocity
                    tracking_params['velocity'] = math.sqrt(
                        tracking_params['velocity_x']**2 + tracking_params['velocity_y']**2
                    )
                    # 运动方向角度(弧度)/Movement direction angle (radians)
                    tracking_params['movement_direction'] = math.atan2(
                        tracking_params['velocity_x'], tracking_params['velocity_y']
                    )
                    # 判断是否在移动/Determine if moving
                    tracking_params['is_moving'] = abs(tracking_params['velocity']) > 5  # 速度阈值5cm/s/Velocity threshold 5cm/s
            
            # 记录当前信息为历史数据/Record current information as historical data
            self.last_center_x = center_x
            self.last_distance = distance
            self.last_update_time = current_time
            
            self.logger.info(f"检测到人体/Detected human body: 中心位置(central location)={center_x}, 距离(dis)={distance}cm, "
                            f"水平速度(x_speed)={tracking_params['velocity_x']:.1f}cm/s, "
                            f"垂直速度(y_speed)={tracking_params['velocity_y']:.1f}cm/s, "
                            f"总速度(Total speed)={tracking_params['velocity']:.1f}cm/s, "
                            f"方向角(azimuth)={math.degrees(tracking_params['movement_direction']):.1f}°")
        else:
            self.logger.warning("未检测到人体/No human detected")
            # 重置历史数据/Reset historical data
            self.last_center_x = None
            self.last_distance = None
            
        return tracking_params
    
    def predict_future_position(self, current_params, prediction_time=0.5):
        """
        预测未来时间点的人体位置
        Predict human position at a future time point
        
        参数/Parameters:
            current_params: 当前追踪参数/Current tracking parameters
            prediction_time: 预测时间(秒)/Prediction time (seconds)
            
        返回/Returns:
            预测的位置信息/Predicted position information
        """
        if not current_params['is_detected'] or not current_params['is_moving']:
            return None
            
        # 预测未来距离(前后方向)/Predict future distance (front-back direction)
        predicted_distance = current_params['distance'] + current_params['velocity_y'] * prediction_time
        
        # 预测水平方向移动(左右方向)/Predict horizontal movement (left-right direction)
        # 将速度(cm/s)转换为像素/秒/Convert velocity (cm/s) to pixels/second
        cm_to_pixel = 100 / 10.0  # 在1米距离下，10厘米对应100像素/At 1 meter distance, 10 cm corresponds to 100 pixels
        horizontal_movement = current_params['velocity_x'] * prediction_time * cm_to_pixel * (predicted_distance / 100)
        
        predicted_center_x = self.last_center_x + horizontal_movement
        
        # 确保预测的中心位置在画面范围内/Ensure predicted center position is within frame bounds
        predicted_center_x = max(0, min(self.camera_width, predicted_center_x))
        
        return {
            'center_x': predicted_center_x,
            'distance': predicted_distance,
            'prediction_time': prediction_time
        }

# 使用示例/Usage example
if __name__ == "__main__":
    # 创建追踪器实例/Create tracker instance
    tracker = HumanTracker()
    
    # 模拟人体数据/Simulate human data
    human_data = [
        {'center_x': 320, 'distance': 200},
        {'center_x': 340, 'distance': 190},  # 向右前方移动/Move to right front
        {'center_x': 360, 'distance': 185},  # 继续向右前方移动/Continue moving to right front
        {'center_x': 350, 'distance': 175},  # 向左前方移动/Move to left front
        {'center_x': 330, 'distance': 170},  # 继续向左前方移动/Continue moving to left front
        None,  # 丢失目标/Lost target
        {'center_x': 320, 'distance': 200}   # 重新检测到/Redetected
    ]
    
    # 模拟追踪过程/Simulate tracking process
    for i, data in enumerate(human_data):
        print(f"\n=== 更新 {i+1} ===/=== Update {i+1} ===")
        tracking_params = tracker.update(data)
        
        # 预测未来位置/Predict future position
        if tracking_params['is_detected'] and tracking_params['is_moving']:
            prediction = tracker.predict_future_position(tracking_params, 0.06)
            if prediction:
                print(f"预测0.06秒后位置: 中心={prediction['center_x']:.1f}, 距离={prediction['distance']:.1f}cm")
                print(f"Predicted position after 0.06s: center={prediction['center_x']:.1f}, distance={prediction['distance']:.1f}cm")
        
        # 模拟时间流逝/Simulate time passing