# 智能管家犬下一阶段低风险功能需求文档

## 1. 背景

当前最小闭环已经完成:

```text
人脸识别主人 -> 语音触发任务 -> 抓红球 -> 找黑线对齐 -> 巡线
```

下一阶段目标不是一次性完成最终版智能管家犬,而是在不破坏现有闭环稳定性的前提下,补齐几项低风险能力:

```text
语音任务槽位解析 -> 任务状态展示 -> 巡线二维码到站 -> 第一版返航复位 -> 配置化
```

这些功能优先复用现有课程代码和当前 `14.housekeeper_minimal` 架构。暂不重构为全局单 `Picamera2` 视觉调度器,也暂不实现交付给人。

## 2. 本阶段目标

### 2.1 用户可见目标

主人通过人脸认证后,可以说出更接近最终演示的任务指令,例如:

```text
把红球送到门口
把红色方块送到客厅
去拿红球到 station_B
开始任务
```

机器狗应能:

1. 从语音中解析目标颜色和目标站点。
2. 通过 TTS、LCD 表情、网页仪表盘复述和展示任务。
3. 完成当前已支持的抓红球流程。
4. 巡线过程中识别二维码站点,到目标站点后停车。
5. 第一版返航:到站后不做交付动作,直接掉头,沿线返回 `home` 二维码站点并复位。

### 2.2 工程目标

1. 保持现有 `housekeeper_main.py -> grab_then_follow_line.py -> ball_grab_v3.py / find_and_align_line.py / follow_line.py` 的子进程串联方式。
2. 不引入新的云服务、模型或重型依赖。
3. 所有新增解析逻辑和命令组装逻辑必须有本地单元测试。
4. 实机相关能力允许通过参数关闭,便于分阶段调试。

## 3. 范围

### 3.1 本阶段包含

- 语音任务槽位解析:颜色、站点、兼容旧触发词。
- 任务状态展示:任务文本、目标颜色、目标站点。
- 巡线二维码到站:识别 `home`、`station_A`、`station_B` 等二维码内容。
- 第一版返航复位:到站后掉头,巡线回 `home`,执行复位。
- 配置文件第一版:站点表、颜色别名、默认任务、超时参数。

### 3.2 本阶段不包含

- 多颜色稳定抓取。当前仍以红球/红色物体为实际抓取目标。
- 抓取后的视觉校验重试。保留现有 `ball_grab_v3.py` 的成功结果文件判断。
- 手势备份指令通道。
- 到站后交付给人、张手确认、地面降级放置。
- 全局单 `Picamera2` 视觉调度器。
- 机械臂交货动作标定。

## 4. 术语与任务数据

### 4.1 任务槽位

任务由以下字段组成:

| 字段 | 类型 | 示例 | 说明 |
|---|---|---|---|
| `raw_text` | 字符串 | `把红球送到门口` | 原始语音识别文本 |
| `requested_color` | 枚举 | `green` | 用户语音中请求的颜色;未说颜色时使用默认 `red` |
| `effective_color` | 枚举 | `red` | 本阶段实际执行的颜色;非红色请求会降级为 `red` |
| `target_station` | 字符串 | `station_B` | 巡线目标二维码内容 |
| `station_label` | 字符串 | `门口` | TTS 和仪表盘展示名称 |

### 4.2 颜色别名

第一版内置以下颜色解析:

| 颜色 | 别名 |
|---|---|
| `red` | 红、红色、红球、红色方块 |
| `green` | 绿、绿色、绿球、绿色方块 |
| `blue` | 蓝、蓝色、蓝球、蓝色方块 |
| `yellow` | 黄、黄色、黄球、黄色方块 |

本阶段实际抓取只支持 `red`。如果解析到非红色,系统应明确播报:

```text
我现在只会拿红球,先按红球执行
```

然后将 `effective_color` 设为 `red`,继续执行。`requested_color` 保留用户原始请求,用于日志和后续多色抓取升级。这样可以先验证语音、站点和返航闭环,不把风险扩散到多色抓取。

### 4.3 站点别名

第一版内置以下站点解析:

| 站点 | 别名 | 二维码内容 |
|---|---|---|
| 待命区 | 家、回家、起点、待命区 | `home` |
| 客厅 | 客厅、A点、站点A、stationA、station_A | `station_A` |
| 门口 | 门口、门边、B点、站点B、stationB、station_B | `station_B` |

`home` 只作为返航目标使用。用户任务中如果说“送回家”,第一版可接受,但会被视为目标站点 `home`。

### 4.4 默认值

为了兼容现有演示流程:

- 只说“开始任务”“去捡球”“拿球”时,默认任务为 `red + station_A`。
- 只说颜色但没有站点时,默认站点为 `station_A`。
- 只说站点但没有颜色时,默认颜色为 `red`。

## 5. 功能需求

### FR1: 语音任务槽位解析

`housekeeper_main.py` 当前的 `parse_task_trigger()` 只返回任务触发结果。下一阶段需要扩展为结构化任务解析:

```text
输入: 任意 ASR 文本
输出: HousekeeperTask 或 None
```

解析规则:

1. 先执行现有停止/继续控制解析。停止/继续优先级高于任务触发。
2. 命中明确任务词,或命中“动词 + 球/方块/物品”,才创建任务。
3. 从文本中解析颜色;未解析到则用默认 `red`。
4. 从文本中解析站点;未解析到则用默认 `station_A`。
5. 对非红色目标保留 `requested_color`,同时将 `effective_color` 降级为 `red`,供后续 TTS 提示。
6. “眼球”“进球”“别停”“不要停”等当前已规避的误触发规则不能回退。

验收标准:

- `把红球送到门口` -> `requested_color=red`, `effective_color=red`, `target_station=station_B`。
- `把绿色方块送到客厅` -> `requested_color=green`, `effective_color=red`, `target_station=station_A`,并播报能力限制。
- `开始任务` -> `requested_color=red`, `effective_color=red`, `target_station=station_A`。
- `眼球` -> 不触发任务。
- `停止` -> 控制命令,不是任务。

### FR2: 任务状态展示和复述

当任务解析成功后,系统应输出统一任务摘要:

```text
收到,去拿红球送到门口
```

展示位置:

1. 控制台日志。
2. TTS 播报。
3. 网页仪表盘任务文本。
4. LCD 表情继续使用现有 `listen/work/success/fail/pause`。

网页仪表盘第一版不需要新增复杂布局;只需确保当前任务文本包含颜色和站点即可。

验收标准:

- 用户说 `把红球送到门口` 后,仪表盘任务文本出现 `红球 -> 门口` 或等价内容。
- 用户说非红色目标时,TTS 至少播报一次当前能力限制。
- 任务开始、到站、返航、完成都产生日志和 TTS 反馈。

### FR3: 巡线时识别二维码目标站点

`follow_line.py` 当前负责持续巡线,下一阶段需要支持目标站点停车。

新增行为:

1. 支持参数 `--target-station station_B`。
2. 每帧巡线时同步尝试二维码识别。
3. 识别到非目标站点时继续巡线,并打印日志:

```text
STATION_SEEN station_A
```

4. 识别到目标站点时:

```text
STATION_REACHED station_B
```

然后停止机器狗,写入结果文件并以退出码 `0` 结束巡线进程。

5. 如果未传 `--target-station`,保持当前无限巡线/按键退出行为。

二维码识别优先复用课程中的 `pyzbar.decode(gray)` 方式。为了降低算力压力,第一版允许每隔 3 帧或 5 帧识别一次二维码。

结果文件建议:

```text
/home/pi/xgoPictures/housekeeper/line_result.json
```

示例内容:

```json
{
  "success": true,
  "target_station": "station_B",
  "reached_station": "station_B",
  "mode": "outbound",
  "timestamp": "20260703_213000"
}
```

验收标准:

- 巡线经过 `station_A`,目标为 `station_B` 时不停。
- 巡线读到 `station_B` 时停车并退出。
- 目标站点参数为空时,旧的巡线行为保持兼容。
- 二维码库不可用时,程序打印明确错误并继续按旧巡线逻辑运行,不直接崩溃。

### FR4: 第一版返航复位

到达目标站点后,本阶段不做交付动作,直接进入返航。

返航流程:

```text
目标站点停车 -> TTS "到达门口" -> 原地掉头 -> 以 home 为目标巡线 -> 读到 home -> 停车 -> dog.reset() -> TTS "任务完成"
```

第一版约束:

1. 掉头可先使用固定时间/速度参数,通过配置文件调整。
2. 返航复用 `follow_line.py --target-station home`。
3. 如果返航过程中超过配置的最大秒数仍未到 `home`,系统停车并播报:

```text
我没有找到回家的路,请帮我一下
```

4. 返航失败时不继续重复尝试,避免机器狗长时间无目标运行。

验收标准:

- `station_B` 到站后会进入返航阶段。
- 读到 `home` 后复位并结束总任务。
- 返航失败会停车、播报、返回非零退出码。

### FR5: 配置文件第一版

新增一个轻量配置文件,使用 Python 标准库可解析的 JSON,避免额外依赖:

```text
14.housekeeper_minimal/housekeeper_config.json
```

建议初始结构:

```json
{
  "defaults": {
    "color": "red",
    "target_station": "station_A"
  },
  "colors": {
    "red": {"label": "红球", "aliases": ["红", "红色", "红球", "红色方块"]},
    "green": {"label": "绿球", "aliases": ["绿", "绿色", "绿球", "绿色方块"]},
    "blue": {"label": "蓝球", "aliases": ["蓝", "蓝色", "蓝球", "蓝色方块"]},
    "yellow": {"label": "黄球", "aliases": ["黄", "黄色", "黄球", "黄色方块"]}
  },
  "stations": {
    "home": {"label": "待命区", "aliases": ["家", "回家", "起点", "待命区"]},
    "station_A": {"label": "客厅", "aliases": ["客厅", "A点", "站点A", "stationA", "station_A"]},
    "station_B": {"label": "门口", "aliases": ["门口", "门边", "B点", "站点B", "stationB", "station_B"]}
  },
  "return_home": {
    "enabled": true,
    "turn_speed": 20,
    "turn_seconds": 2.4,
    "timeout_seconds": 90
  },
  "line": {
    "qr_decode_every_frames": 3,
    "result_path": "/home/pi/xgoPictures/housekeeper/line_result.json"
  }
}
```

验收标准:

- 配置文件缺失时使用内置默认值。
- 配置文件格式错误时打印错误并退出,不进入真实动作流程。
- 单元测试覆盖配置加载、默认值、别名解析。

## 6. 状态流

下一阶段总状态流:

```text
FACE_AUTH
  -> LISTEN
  -> TASK_CONFIRMED
  -> GRAB
  -> FIND_AND_ALIGN_LINE
  -> TRANSPORT_TO_STATION
  -> ARRIVED_STATION
  -> TURN_HOME
  -> RETURN_HOME
  -> DONE
```

失败状态:

```text
AUTH_FAILED
NO_TASK
GRAB_FAILED
ALIGN_FAILED
STATION_NOT_REACHED
RETURN_HOME_FAILED
```

每次状态变化必须至少产生一条控制台日志;关键状态变化需要 TTS 和仪表盘反馈。

## 7. 命令行参数需求

### 7.1 housekeeper_main.py

新增或调整参数:

```text
--config PATH
--disable-return-home
--target-station station_B       # 调试用,覆盖语音解析结果
--target-color red               # 调试用,覆盖 effective_color
```

### 7.2 grab_then_follow_line.py

新增或调整参数:

```text
--target-station station_B
--return-home
--home-station home
--return-timeout 90
--turn-home-speed 20
--turn-home-seconds 2.4
```

### 7.3 follow_line.py

新增或调整参数:

```text
--target-station station_B
--line-result /home/pi/xgoPictures/housekeeper/line_result.json
--line-mode outbound|return
--qr-decode-every-frames 3
```

## 8. 测试需求

### 8.1 本地单元测试

必须覆盖:

1. 语音文本解析到颜色和站点。
2. 默认值兼容旧触发词。
3. 非红色目标降级提示。
4. 停止/继续控制命令优先级。
5. 配置文件加载和别名解析。
6. `grab_then_follow_line.py` 组装 outbound 和 return 两段巡线命令。
7. `follow_line.py` 中二维码结果处理函数:目标站点、非目标站点、无二维码。

### 8.2 实机冒烟测试

按以下顺序测试:

1. `--dry-voice` 输入 `把红球送到门口`,确认解析和播报。
2. `follow_line.py --target-station station_B` 单独巡线,确认经过 `station_A` 不停,到 `station_B` 停。
3. `grab_then_follow_line.py --skip-grab --skip-align --target-station station_B --return-home` 测试站点到站和返航。
4. `housekeeper_main.py --dry-voice --disable-voice-control` 运行完整调试路径。
5. 去掉 dry 参数,跑真实语音路径。

## 9. 风险与约束

| 风险 | 影响 | 本阶段处理 |
|---|---|---|
| 二维码识别和巡线抢算力 | 巡线变慢或抖动 | 每隔 N 帧识别一次二维码 |
| 返航固定掉头角度不准 | 回程上线失败 | 参数配置化,先实地标定 |
| 非红色目标被用户误以为已支持 | 演示误解 | 明确播报“现在只会拿红球” |
| `pyzbar` 在机器狗环境不可用 | 到站能力不可用 | 启动时打印清晰提示,保留旧巡线 |
| 配置文件写错 | 实机流程异常 | 启动前校验,错误时不进入动作流程 |

## 10. 交付标准

本阶段完成后,至少满足:

1. 本地单元测试全部通过。
2. 旧命令 `开始任务` 仍可触发当前红球抓取巡线流程。
3. 新命令 `把红球送到门口` 可以驱动目标站点为 `station_B` 的巡线停车。
4. 到达目标站点后可以进入第一版返航,读到 `home` 后复位结束。
5. README 更新本阶段启动方式和调试命令。

## 11. 建议实施顺序

1. 增加配置加载和任务解析单元测试。
2. 实现结构化 `HousekeeperTask` 和任务摘要。
3. 给 `follow_line.py` 增加二维码识别到站的纯函数与测试。
4. 接入 `follow_line.py` 命令行参数和结果文件。
5. 修改 `grab_then_follow_line.py` 传递目标站点并增加返航流程。
6. 修改 `housekeeper_main.py` 读取配置、展示任务、传参给子流程。
7. 更新 README。
8. 实机分段验证。
