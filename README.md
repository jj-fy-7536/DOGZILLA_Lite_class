# 「智能管家犬」需求文档

基于 DOGZILLA Lite(XGO Lite)机器狗与 `DOGZILLA_Lite_class` 课程代码库

---

## 主要实现代码

当前智能管家犬的**主要功能实现**位于:

```text
6.AI Visual Interaction Course/14.housekeeper_minimal/
```

| 文件 | 说明 |
|---|---|
| `housekeeper_main.py` | 第一版总控: 人脸识别 → 语音解析任务 → 抓球 → 巡线到站 → 返航 |
| `face_interaction.py` | 阶段 1: 人脸检测、主人录入、主人/陌生人识别 |
| `voice_interaction.py` | 语音控制、任务槽位解析、天气查询 |
| `grab_then_follow_line.py` | 抓球后找线对齐并巡线到目标站点 |
| `web_dashboard.py` | 电脑端网页仪表盘 |

更详细的运行说明见 [`14.housekeeper_minimal/README.md`](6.AI%20Visual%20Interaction%20Course/14.housekeeper_minimal/README.md)。

在机器狗上启动总控:

```bash
cd "/home/pi/DOGZILLA_Lite_class/6.AI Visual Interaction Course/14.housekeeper_minimal"
. /home/pi/dogzilla_runs/voice_env.sh
export PYTHONPATH=/home/pi/RaspberryPi-CM5/app:/home/pi/RaspberryPi-CM5/demos:.
/home/pi/RaspberryPi-CM5/xgovenv/bin/python -u housekeeper_main.py --robot-ip <机器狗IP>
```

---

## 1. 项目概述

### 1.1 一句话描述

机器狗平时在"家"中待命,能认出主人、听懂语音指令、看懂手势,可完成 **"找到指定颜色物品 → 机械臂抓取 → 沿引导线运输 → 送到指定的人手中"** 的全流程任务,全程有表情、语音和状态反馈,遇到异常(找不到目标、抓取失败、无人接收)能自动重试、降级或报告。

### 1.2 设计原则

- **只用现成能力**:所有单项功能均来自 `DOGZILLA_Lite_class` 课程中已验证的代码,不发明新算法,难度集中在系统集成;
- **闭环可靠**:每个阶段定义明确的成功/失败条件与对应处理策略;
- **参数配置化**:HSV 阈值、站点表、人脸库、机械臂角度等全部外置为配置文件,换场地不改代码。

### 1.3 硬件依赖

| 硬件 | 用途 |
|---|---|
| XGO Lite 机器狗本体 | 四足运动、姿态控制 |
| 机械臂 + 夹爪 | 抓取与交付物品 |
| 树莓派 + 摄像头(Picamera2) | 所有视觉任务 |
| 2 寸 LCD 屏 | 表情与状态显示 |
| 麦克风 / 扬声器 | 语音识别(讯飞云 API)与 TTS 播报 |
| 机身按键 | 紧急停止、调试切换 |

### 1.4 场地布置

- 地面贴一条彩色胶带作为**引导线**,连接"待命区"与各"站点";
- 沿线贴**二维码路标**:`home`(起点/待命区)、`station_A`(客厅)、`station_B`(门口)等;
- 待抓取物品为课程适配尺寸的**彩色木块**(红/绿/蓝/黄四色,HSV 阈值课程中现成);
- 光照相对稳定(HSV 颜色识别对光照敏感,需现场用调参工具标定)。

---

## 2. 任务流程需求(六个阶段)

### 阶段 1:身份识别与唤醒(门禁)

**任务描述**

- 待命状态:屏幕显示睡觉表情,摄像头以低帧率运行人脸检测;
- 检测到有人靠近后,运行人脸识别判断身份:
  - **主人** → 表情切换为开心,TTS 播报"主人你好",进入阶段 2(聆听);
  - **陌生人** → 表情切换为警惕,TTS 播报"你是谁",保持待命;
  - (可选加分项)对陌生人运行情绪识别,附加播报如"你看起来心情不错"。

**验收标准**:主人识别准确率 ≥ 90%,陌生人不会误触发聆听状态。

**代码模块**

| 功能 | 课程模块路径 | 说明 |
|---|---|---|
| 人脸检测 | `5.AI Visual Recognition Course/09. Face detection` | 待命时的低成本检测 |
| 人脸识别 | `5.AI Visual Recognition Course/18. Face Recognition` | 需预先录入主人人脸库 |
| 情绪识别(可选) | `5.AI Visual Recognition Course/19. Emotion_Recognition` | `emotion_demo.ipynb` |
| 屏幕表情 | `2.Base Control/12.show expression` | `show_expression.ipynb` |
| TTS 播报 | `2.Base Control/04.Text synthesized audio` | `dog_tts.ipynb` |
| 摄像头驱动 | `5.AI Visual Recognition Course/01. Camera driver` | `test1.py`,Picamera2 基础用法 |

---

### 阶段 2:多模态指令接收

**任务描述**

- **语音通道(主)**:录音 4 秒 → 讯飞语音识别 → 解析出两个槽位:
  - 目标物颜色:红 / 绿 / 蓝 / 黄;
  - 收货站点:如"客厅"→`station_A`、"门口"→`station_B`;
  - 示例指令:"把红色方块送到门口";
- **手势通道(备份)**:语音连续 2 次识别失败时切换,用手指数量选择目标颜色(1=红,2=绿,3=蓝,4=黄),站点默认 `station_A`;
- 解析成功后 TTS **复述任务**("收到,去拿红色方块送到门口"),屏幕切换"工作中"表情,进入阶段 3;
- 30 秒内未收到有效指令 → 回到阶段 1 待命。

**验收标准**:安静环境下语音槽位解析成功率 ≥ 80%;手势通道可用作完整替代。

**代码模块**

| 功能 | 课程模块路径 | 说明 |
|---|---|---|
| 录音 | `2.Base Control/02.Record audio` | `dog_recode_voice.ipynb`,`XGOEDU.xgoAudioRecord` |
| 语音识别 | `2.Base Control/05.speech to text` | **`voice_sit.py` 为核心参考**,含完整讯飞 WebSocket 流程,把 `is_sit_command` 扩展为槽位解析 |
| 手指识别 | `5.AI Visual Recognition Course/14. Finger recognition` | MediaPipe 手部关键点数手指 |
| TTS 复述 | `2.Base Control/04.Text synthesized audio` | 同上 |
| 屏幕表情 | `2.Base Control/12.show expression` | 同上 |

---

### 阶段 3:搜索与抓取(带校验重试)

**任务描述**

- **搜索**:原地分段旋转(如每次 45°),每段停下扫描画面中是否存在目标颜色色块;
  - 旋转累计 360° 仍未找到 → TTS 报告"没有找到红色方块",回阶段 2;
- **接近**:找到后以 PID 控制机身左右对准色块(图像 X 轴中心 160),前进逼近,直至色块 Y 坐标进入抓取窗口(205~208);
- **抓取**:执行机械臂抓取序列——俯身 → 张爪 → 下压 → 合爪 → 抬臂 → 站立;
- **抓取校验**(关键需求):抬臂后再次检测夹爪区域是否仍有目标颜色:
  - 有 → 抓取成功,进入阶段 4;
  - 无 → 判定抓空,自动重试,**最多 3 次**;3 次均失败 → TTS 求助"我抓不到,请帮帮我",回阶段 2。

**验收标准**:单次抓取成功率 ≥ 60%,含重试的总成功率 ≥ 90%。

**代码模块**

| 功能 | 课程模块路径 | 说明 |
|---|---|---|
| 颜色定位+PID 对准+抓取全流程 | `6.AI Visual Interaction Course/11.pick it up` | **核心复用**:`pick_it_up.py` 的 `g_mode 1/2` 状态逻辑、四色 HSV 阈值、PID 参数(Px=0.25)、机械臂抓取序列(`motor([52,53],...)` + `claw(pos)`)全部现成 |
| PID 控制器 | 同上目录 `PID.py` | `PositionalPID` |
| 机械臂/夹爪参数标定 | `3.Dog Base Control/08.Puppy robotic arm control` | `robotic_arm_control.ipynb`,滑块交互式调 `motor`/`arm`/`claw` 参数 |
| 姿态与身高调整 | `3.Dog Base Control/03.Dog Adjust height and posture` | 抓取前 `translation(['z'],[75])` + `attitude(['p'],[15])` 俯身姿态 |
| 转向搜索 | `3.Dog Base Control/02.Dog_Trun` | `dog.turn()` 分段旋转 |
| HSV 现场标定 | `5.AI Visual Recognition Course/03. HSV value test` | 换场地/光照时重新标定四色阈值 |

---

### 阶段 4:巡线运输

**任务描述**

- 叼着木块从抓取点走到引导线上(简化:抓取区就设在引导线旁,固定转身角度即可上线);
- 沿彩色引导线行走,步态用 `slow` 保证稳定不掉块;
- 行走中**每帧同时运行二维码检测**,读到路标时与任务站点比对:
  - 是目标站点 → `dog.stop()`,进入阶段 5;
  - 不是 → 继续巡线;
- 巡线中丢线超过 3 秒 → 停下,原地小幅摆动重新找线;找回失败 → TTS 报告"我迷路了",原地等待救援。

**验收标准**:3 米引导线 + 2 个站点场景下,到站准确率 ≥ 90%,运输途中木块不脱落。

**代码模块**

| 功能 | 课程模块路径 | 说明 |
|---|---|---|
| 巡线 | `6.AI Visual Interaction Course/01.color_line` | **核心复用**:`follow_line.py`、`line_tracker.py`,含调试流 `line_debug_stream.py` |
| 巡线 HSV 调参 | 同上目录 `HSV_Config_Two.py` / `test_hsv_config_two.py` | 现场标定引导线颜色 |
| 二维码识别 | `6.AI Visual Interaction Course/06.QR code recognition` 及 `5.AI Visual Recognition Course/05. QR code recognition` | 站点路标读取 |
| 步态/速度控制 | `3.Dog Base Control/01.Dog_move` | `gait_type('trot')` + `pace('slow')` |

---

### 阶段 5:交付给人

**任务描述**

- 到站后抬头(调整 pitch 姿态),运行人脸检测 + 人体跟随微调朝向,对准收货人;
- 等待收货人做出**张开手掌**的手势作为"确认接收"信号;
- 检测到确认手势 → 机械臂前伸 → 松爪交货 → TTS 播报"给你";
- **超时降级**:10 秒内未检测到人或手势 → 俯身把木块放在地面,TTS 播报"东西放这里了";
- 交付完成后机械臂复位。

**验收标准**:有人配合时手势交付成功率 ≥ 80%;无人时能正确降级放置。

**代码模块**

| 功能 | 课程模块路径 | 说明 |
|---|---|---|
| 人脸追踪对准 | `6.AI Visual Interaction Course/07.Facial tracking` | 到站后微调朝向 |
| 人体跟随 | `6.AI Visual Interaction Course/13.Human Body Follows` | 备用:人不在正前方时先跟随靠近 |
| 手势识别(张开手掌) | `5.AI Visual Recognition Course/15. Gesture recognition` | `hands_detect.ipynb`,MediaPipe |
| 机械臂交货动作 | `3.Dog Base Control/08.Puppy robotic arm control` | `arm(x,z)` 前伸 + `claw(0)` 松爪 |
| 姿态抬头 | `3.Dog Base Control/03.Dog Adjust height and posture` | `attitude(['p'],[-N])` |

---

### 阶段 6:返航复位

**任务描述**

- 原地掉头(`turn` 约 180°),重新上线,沿引导线反向行走;
- 读到 `home` 二维码 → 停下,`dog.reset()` 复位全部姿态与机械臂;
- 屏幕切回待命表情,TTS 播报"任务完成";
- 状态机回到阶段 1。

**验收标准**:返航到站率 ≥ 90%,复位后可立即接受下一次任务。

**代码模块**

| 功能 | 课程模块路径 | 说明 |
|---|---|---|
| 巡线返程 | 复用阶段 4 全部模块 | 方向相反 |
| 掉头 | `3.Dog Base Control/02.Dog_Trun` | `dog.turn()` 定时旋转 |
| 复位 | `xgolib` 基础 API | `dog.reset()` |
| 电量自检(可选) | `2.Base Control/10.Read Battery` | 电量低于阈值时 TTS 提醒充电 |

---

## 3. 系统架构需求

```
┌───────────────────────────────────────────────┐
│                  主状态机 (main.py)              │
│  IDLE → AUTH → LISTEN → SEARCH → GRAB(×3重试)   │
│   → TRANSPORT → DELIVER → RETURN → IDLE        │
└───────────────┬──────────┬──────────┬─────────┘
                │          │          │
        ┌───────▼───┐ ┌────▼─────┐ ┌──▼────────┐
        │ 视觉调度器  │ │ 动作接口层 │ │  反馈层    │
        │ vision.py  │ │ motion.py │ │ feedback.py│
        └───────────┘ └──────────┘ └───────────┘
```

### 3.1 主状态机(main.py)

- 全局唯一的状态变量与状态迁移表,每个状态定义:进入动作、循环体、成功迁移、失败迁移、超时迁移;
- 机身按键 B 为**全局急停**(任何状态下按下 → `dog.stop()` + 复位 + 回 IDLE),参考 `pick_it_up.py` 中 `button.press_b()` 的用法。

### 3.2 视觉调度器(vision.py)

摄像头全局只有一个 Picamera2 实例,**这是工程上最容易翻车的地方**,需求如下:

- Picamera2 在程序启动时初始化一次,全程不重复 start/stop;
- 各识别器(人脸/颜色/二维码/手势)封装为统一接口 `detector.process(frame) -> result`,由状态机按当前阶段决定每帧把画面喂给哪些识别器;
- MediaPipe 模型(手势/人体)按需懒加载,阶段切换时显式释放,避免树莓派内存耗尽;
- 每帧处理结果同步绘制到 LCD(参考 `pick_it_up.py` 的 `display.ShowImage` 流程),便于现场调试。

### 3.3 动作接口层(motion.py)

- 封装 `xgolib.XGO`(`port='/dev/ttyAMA0', version='xgolite'`)为单例;
- 提供高层动作原语:`search_turn(angle)`、`approach(pid_output)`、`grab_sequence()`、`deliver_sequence()`、`follow_line_step(offset)`、`reset_all()`;
- 所有机械臂角度、姿态数值来自配置文件,不硬编码。

### 3.4 反馈层(feedback.py)

- 表情 + TTS + 控制台日志三通道统一出口:`notify(state, message)`;
- 每次状态迁移必须产生一条反馈,保证演示时观众能理解狗"在想什么"。

### 3.5 配置文件(config.yaml)

```yaml
colors:            # 四色 HSV 阈值(现场标定后覆盖)
  red:   {lower: [0,43,46],   upper: [10,255,255]}
  green: {lower: [35,43,46],  upper: [77,255,255]}
  blue:  {lower: [100,43,46], upper: [124,255,255]}
  yellow:{lower: [26,43,46],  upper: [34,255,255]}
line_color: {...}  # 引导线 HSV
stations:          # 二维码内容 → 站点名
  home: "待命区"
  station_A: "客厅"
  station_B: "门口"
grab:              # 抓取参数(来自 pick_it_up.py,现场微调)
  claw_pos: 210
  approach_window: [205, 208]
  pid: {p: 0.25, i: 0, d: 0.0001}
  max_retry: 3
timeouts:
  listen: 30       # 秒
  deliver: 10
faces_db: ./faces/ # 主人人脸库目录
```

---

## 4. 异常处理矩阵

| 阶段 | 异常 | 处理策略 |
|---|---|---|
| 认人 | 人脸识别置信度低 | 按陌生人处理,不误开聆听 |
| 聆听 | 语音识别失败 ×2 | 降级到手势通道 |
| 聆听 | 30 秒无有效指令 | 回待命 |
| 搜索 | 旋转 360° 未找到目标 | TTS 报告,回聆听 |
| 抓取 | 校验发现抓空 | 自动重试,最多 3 次 |
| 抓取 | 3 次全部失败 | TTS 求助,回聆听 |
| 巡线 | 丢线 > 3 秒 | 停下摆动找线;失败则 TTS 报告并原地等待 |
| 交付 | 10 秒无人/无手势 | 降级为地面放置 + TTS 播报 |
| 全局 | 按键 B 按下 | 急停,全部复位,回待命 |
| 全局 | 电量低(可选) | TTS 提醒充电,拒接新任务 |

---

## 5. 开发里程碑(6~8 周)

| 里程碑 | 周期 | 内容 | 交付物 |
|---|---|---|---|
| M1 认人+聆听 | 第 1-2 周 | 阶段 1、2:人脸库录入、语音槽位解析、手势备份通道、表情/TTS 反馈 | 说指令 → 狗复述任务 |
| M2 搜索+抓取 | 第 3-4 周 | 阶段 3:改造 `pick_it_up.py`,加入旋转搜索、抓取校验与重试 | 语音指定颜色 → 抓起对应木块 |
| M3 巡线寻址 | 第 5-6 周 | 阶段 4、6:巡线 + 二维码站点识别 + 返航 | 叼着木块从 home 走到 station 再返回 |
| M4 交付+打磨 | 第 7-8 周 | 阶段 5:手势确认交付、全链路异常处理、配置化、完整联调 | 端到端完整演示 |

**依赖关系**:M1 与 M2 可并行(不同人负责),M3 依赖 M2(需要叼着块巡线测稳定性),M4 依赖全部。

---

## 6. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 光照变化导致 HSV 失效 | 搜索/巡线失败 | 每次换场地用 `HSV value test` / `HSV_Config_Two.py` 重新标定;演示场地固定灯光 |
| 抓取成功率不稳定 | 核心环节卡壳 | 木块尺寸固定;用 `robotic_arm_control.ipynb` 精调参数;靠校验+重试兜底 |
| 树莓派算力不足(多模型) | 掉帧、卡顿 | 视觉调度器保证同一时刻只跑必要的识别器;分辨率固定 320×240 |
| 讯飞 API 依赖网络 | 语音不可用 | 手势通道全程可替代语音;密钥放环境变量 |
| 叼块行走时掉落 | 运输失败 | 用 `slow` 步态;夹爪 `claw_pos` 收紧值现场标定 |

---

## 7. 验收演示脚本

1. 狗在 `home` 待命,屏幕睡觉表情;
2. 主人走近 → 狗醒来问好;陌生人走近 → 狗发出警惕提示(对比演示);
3. 主人说"把红色方块送到门口" → 狗复述任务;
4. 狗旋转搜索、走近、抓起红色木块(可故意先抓空一次,展示自动重试);
5. 狗沿引导线行走,经过 `station_A` 不停,到 `station_B` 停下;
6. 客人张开手掌 → 狗前伸机械臂交货;
7. 狗掉头沿线返回 `home`,复位,播报"任务完成"。
