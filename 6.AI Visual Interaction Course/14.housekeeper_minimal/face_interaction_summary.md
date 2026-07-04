# 人脸识别模块总结

## 文件

- 代码文件: `face_interaction.py`
- 当前用途: 智能管家犬阶段 1，检测人脸、录入主人、区分主人和陌生人，并在电脑浏览器显示实时画面。

## 运行环境

- 运行位置: 机器狗 Raspberry Pi CM5
- Python 环境: `/home/pi/RaspberryPi-CM5/xgovenv`
- 摄像头库: `picamera2`
- 图像处理: `opencv-python`
- 人脸检测: OpenCV Haar Cascade
- 默认机器狗 IP: `172.20.10.4`
- 实时画面端口: `8090`

## 运行方式

```bash
cd /home/pi/RaspberryPi-CM5
source xgovenv/bin/activate
python -u car/face_interaction.py --robot-ip 172.20.10.4
```

电脑浏览器访问:

```text
http://172.20.10.4:8090/
```

本地启动脚本:

```bash
./run_face.exp
```

## 已实现功能

- 摄像头实时采集，分辨率 `640x480`
- 红蓝通道已修正
- 浏览器 MJPEG 实时预览
- Haar 人脸检测
- 桌面底部区域过滤，减少充电宝等物体误识别为人脸
- 网页按钮“保存主人脸”
- 主人人脸库存储与自动重载
- 主人/陌生人识别
- 最近 5 帧投票防抖，避免 OWNER/STRANGER 频繁跳变

## 主人人脸库

机器狗端路径:

```text
/home/pi/RaspberryPi-CM5/car/faces/owner/
```

本地已导出路径:

```text
owner_faces_export/owner/
```

保存格式:

```text
owner_YYYYMMDD_HHMMSS.jpg
```

## 关键参数

```python
FACE_MIN_SIZE = (80, 80)
FACE_MAX_CENTER_Y_RATIO = 0.78
IDENTITY_HISTORY_SIZE = 5
OWNER_CONFIRM_COUNT = 2
STRANGER_CONFIRM_COUNT = 4
threshold = 0.40
port = 8090
```

参数含义:

- `FACE_MIN_SIZE`: 最小人脸尺寸，小于该尺寸不识别。
- `FACE_MAX_CENTER_Y_RATIO`: 人脸中心点超过画面高度 78% 时丢弃，用来过滤桌面物体。
- `IDENTITY_HISTORY_SIZE`: 最近几帧参与投票。
- `OWNER_CONFIRM_COUNT`: 最近 5 帧中至少 2 帧识别为 owner 才确认主人。
- `STRANGER_CONFIRM_COUNT`: 最近 5 帧中至少 4 帧识别为 stranger 才确认陌生人。
- `threshold`: 人脸相似度门槛，越低越容易判成主人。

## 主要类和函数

- `Camera`: 打开 Picamera2，并返回 OpenCV 可用的 BGR 图像。
- `FaceDetector`: 负责从画面中找人脸框。
- `FaceRecognizer`: 负责人脸库加载、主人样本保存、主人/陌生人判断。
- `VideoStreamer`: 提供浏览器实时画面和“保存主人脸”按钮。
- `annotate`: 在画面上画检测框和状态文本。
- `run`: 主循环，串联摄像头、检测、识别、投票、网页显示。

## 合并时建议拆出的接口

建议在总控程序中保留以下接口:

```python
face_module.start()
face_module.stop()
face_module.get_identity()
face_module.capture_owner()
face_module.get_latest_frame()
```

其中 `get_identity()` 返回:

```python
"owner" | "stranger" | "face" | "waiting"
```

## 合并注意点

- 人脸模块和抓球模块都要使用摄像头，合并时不能同时创建两个 `Picamera2()`。
- 合并时应使用一个统一的摄像头管理器，两个模块共享同一帧图像。
- 人脸模块端口是 `8090`，抓球模块端口是 `8089`，合并后建议只保留一个网页端口。
- `kill_official_services()` 只能在总程序启动时执行一次。
- 当前人脸识别是轻量模板匹配，不是深度学习人脸识别，适合课程演示，不适合强安全身份认证。
