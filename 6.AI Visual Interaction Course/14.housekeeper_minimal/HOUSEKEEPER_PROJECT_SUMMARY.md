# DOGZILLA Lite 智能管家犬项目总结

日期：2026-07-04

## 1. 项目背景

本项目基于亚博 DOGZILLA Lite 机器狗课程代码，在 `6.AI Visual Interaction Course` 中新增并完善了一个“智能管家犬”最小闭环。目标不是做单点 demo，而是把人脸认证、语音交互、抓球、巡线、二维码到站、返航、网页仪表盘、音乐播放、AI 问答和紧急停止整合成一套可以在机器狗上连续运行的总控程序。

当前主项目目录：

```text
6.AI Visual Interaction Course/14.housekeeper_minimal
```

机器狗运行目录通常为：

```text
/home/pi/DOGZILLA_Lite_class/6.AI Visual Interaction Course/14.housekeeper_minimal
```

机器狗地址示例：

```text
pi@172.20.10.4
```

仓库中不保存真实 API key、讯飞密钥或机器狗 SSH 密码。实际密钥应放在机器狗上的：

```text
/home/pi/dogzilla_runs/voice_env.sh
```

## 2. 总体流程

`housekeeper_main.py` 是总控入口，运行流程如下：

```text
人脸识别 owner
  -> 进入 LISTEN 常驻语音监听
  -> 解析任务或普通问答
  -> 抓取指定颜色球
  -> 找黑线并对齐
  -> 巡线识别二维码站点
  -> 到目标站点停车
  -> 可选返航 home
  -> 回到监听状态
```

网页仪表盘默认端口：

```text
http://机器狗IP:8091/
```

仪表盘显示：

- 当前阶段：`FACE_AUTH` / `LISTEN` / `TASK` / `DONE`
- 当前表情：`scan` / `happy` / `listen` / `work` / `success` / `fail` / `pause`
- 当前任务文本，例如 `红球 -> 门口`
- 视频画面流，来自 `frame_bus.py`
- 语音识别和机器狗回答日志，区分“我听到”“机器狗说”“执行”“忽略”
- 右上角“停止任务”按钮

## 3. 已完成能力

### 3.1 多颜色抓球

语音任务可以解析目标颜色，并传给抓球脚本：

```text
把红球送到门口
把绿球送到门口
把蓝球送到客厅
把黄球送到门口
```

支持颜色：

```text
red / green / blue / yellow
```

关键文件：

```text
housekeeper_main.py
grab_then_follow_line.py
housekeeper_config.json
```

抓球核心仍依赖课程中的：

```text
../11.pick it up/ball_grab_v3.py
```

### 3.2 站点与返航

任务可解析站点：

```text
客厅 -> station_A
门口 -> station_B
家 / 回家 -> home
```

巡线通过二维码识别站点。到达目标站点后，可以按配置返航到 `home`。

配置文件：

```text
housekeeper_config.json
```

### 3.3 人脸认证

总控会先启动 `face_interaction.py`，通过主人识别后退出并释放摄像头，再进入语音阶段。

关键参数：

```text
--exit-on-owner
--auth-result /home/pi/xgoPictures/housekeeper/auth_result.json
```

### 3.4 语音识别与常驻监听

语音识别使用讯飞 IAT WebSocket。总控启动后会常驻监听，不再为任务语音和普通语音分别启动多个录音进程，避免 `arecord` 抢麦克风。

关键文件：

```text
voice_interaction.py
echo_guard.py
housekeeper_main.py
```

语音日志会写到仪表盘，方便确认 ASR 到底识别成了什么。

### 3.5 DeepSeek 问答与联网搜索

AI 问答优先使用 DeepSeek Chat Completions API。旧 Spark Lite 仍保留兼容，但只有没有 `DEEPSEEK_API_KEY` 时才使用。

DeepSeek API 本身没有“打开联网”的简单开关，因此本项目实现方式是：

```text
用户问题
  -> 判断是否需要实时信息
  -> 先抓取网页搜索上下文
  -> 把搜索结果和问题一起交给 DeepSeek
  -> 输出适合 TTS 朗读的短回答
```

已加入实时搜索关键词：

```text
今天 / 最近 / 近期 / 实时 / 新闻 / 天气 / 世界杯 / 比分 / 国际形势 / 国际局势 / 国际新闻 / 时事 / 中东 / 俄乌 / 加沙 / 战争 / 冲突 / 股价 等
```

世界杯问题会优先搜索：

```text
FOX 世界杯比分页
ESPN 世界杯赛程页
Olympics 中文世界杯赛程页
Bing / 百度 / Jina 搜索
```

天气已经取消单独 `weather` 指令，不再走本地天气 API。天气相关语音会作为普通问句直接进入 DeepSeek/API：

```text
杭州天气怎么样
杭州目前的天气怎么样
今天天气怎么样
```

### 3.6 更像人的 TTS

新增 `XunfeiTTS`，优先调用讯飞在线语音合成 WebAPI：

```text
wss://tts-api.xfyun.cn/v2/tts
```

默认发音人：

```text
x4_xiaoyan
```

失败时回退到官方 `xgoedu.SpeechSynthesis()`。

### 3.7 音乐播放

音乐播放脚本在机器狗上：

```text
/home/pi/dogzilla_runs/dogzilla_music_player.py
```

默认歌曲目录：

```text
/home/pi/dogzilla_runs/music/
```

语音命令：

```text
放歌 / 播放音乐 / 播放歌曲 / 放音乐 / 听歌 / 来首歌 / 来一首歌 / 唱歌
停歌 / 停止播放 / 停止音乐 / 关音乐 / 关闭音乐 / 别放了
```

注意：

- 单独说“停止”是机器人急停，不是停歌。
- “停歌”才是停止音乐。

### 3.8 噪音过滤与任务中静默

为了解决机器狗运行时噪音大、ASR 乱识别的问题，加入了过滤规则：

- “嗯嗯嗯”“啊啊啊”“好的”“abc”等碎片或噪音不会进入 DeepSeek。
- 机器人自己的 TTS 回声会通过 `EchoGuard` 过滤。
- 任务执行中默认静默，不回答普通问题，不播报任务阶段，只在仪表盘记录。
- 任务执行中只有“停止”可以穿透并急停。

默认参数：

```text
--quiet-during-task
```

如需调试任务过程播报，可以使用：

```text
--no-quiet-during-task
```

### 3.9 停止按钮与语音急停

这是最近重点修复的部分。

旧问题：

- 网页按钮只请求总控软停止。
- 抓球/巡线子进程可能还在运行。
- 机器狗可能继续执行残留动作。

现逻辑：

```text
网页“停止任务”按钮
语音“停止 / 停下 / 急停 / 暂停 / 别动”
  -> emergency_stop_everything()
  -> controller.request_stop()
  -> kill_task_subprocesses()
  -> stop_music_silent()
  -> stop_robot_motion(cycles=8)
```

另外，任务子进程使用独立进程组：

```python
subprocess.Popen(..., start_new_session=True)
```

停止时会对进程组发信号：

```python
os.killpg(process.pid, signal.SIGINT)
```

如果还有残留，会兜底清理：

```text
grab_then_follow_line.py
ball_grab_v3.py
find_and_align_line.py
follow_line.py
```

停止后总控回到监听状态，不自动断点续跑。需要继续任务时，重新说任务即可。

## 4. 关键文件说明

```text
housekeeper_main.py
```

总控入口，负责：

- 人脸 -> 语音 -> 抓球巡线主流程
- 常驻语音监听
- 任务解析
- DeepSeek 问答接入
- 音乐控制
- 网页仪表盘 stop 回调
- 统一急停逻辑

```text
voice_interaction.py
```

独立语音模块，负责：

- 讯飞 IAT 语音识别
- 讯飞在线 TTS
- DeepSeek/Spark 问答
- 网页搜索上下文
- 动作、音乐、问答命令解析
- 噪音和回声过滤

```text
web_dashboard.py
```

网页仪表盘，负责：

- 状态 JSON
- MJPEG 画面流
- 事件日志
- `/stop` POST 按钮接口

```text
grab_then_follow_line.py
```

抓球、找线、巡线、到站、返航的包装流程。

```text
find_and_align_line.py
```

抓球后找黑线并对齐。

```text
echo_guard.py
```

过滤机器狗自己的 TTS 回声，避免把播报识别成新指令。

```text
frame_bus.py
```

跨进程共享最新视频帧，用于仪表盘统一显示人脸、抓球、巡线等阶段画面。

```text
chat_config.py
```

统一解析 DeepSeek/Spark 配置，优先 DeepSeek。

## 5. 环境变量

机器狗上建议创建：

```text
/home/pi/dogzilla_runs/voice_env.sh
```

示例：

```bash
export XFYUN_APPID="你的讯飞APPID"
export XFYUN_API_KEY="你的讯飞APIKey"
export XFYUN_API_SECRET="你的讯飞APISecret"

export XFYUN_TTS_APPID="你的在线语音合成APPID"
export XFYUN_TTS_API_KEY="你的在线语音合成APIKey"
export XFYUN_TTS_API_SECRET="你的在线语音合成APISecret"
export XFYUN_TTS_VCN="x4_xiaoyan"

export DEEPSEEK_API_KEY="YOUR_DEEPSEEK_API_KEY"
export DEEPSEEK_MODEL="deepseek-v4-flash"
export DEEPSEEK_API_URL="https://api.deepseek.com/chat/completions"

# 旧 Spark 兼容配置，可选
export SPARK_API_PASSWORD="YOUR_SPARK_API_PASSWORD"
export SPARK_MODEL="lite"
```

设置权限：

```bash
chmod 600 /home/pi/dogzilla_runs/voice_env.sh
```

## 6. 启动命令

在机器狗上运行总控：

```bash
cd "/home/pi/DOGZILLA_Lite_class/6.AI Visual Interaction Course/14.housekeeper_minimal"
. /home/pi/dogzilla_runs/voice_env.sh
export PYTHONPATH=/home/pi/RaspberryPi-CM5/app:/home/pi/RaspberryPi-CM5/demos:.
/home/pi/RaspberryPi-CM5/xgovenv/bin/python -u housekeeper_main.py --robot-ip 172.20.10.4
```

浏览器打开：

```text
http://172.20.10.4:8091/
```

独立语音模块：

```bash
cd "/home/pi/DOGZILLA_Lite_class/6.AI Visual Interaction Course/14.housekeeper_minimal"
. /home/pi/dogzilla_runs/voice_env.sh
export PYTHONPATH=/home/pi/RaspberryPi-CM5/app:/home/pi/RaspberryPi-CM5/demos:.
/home/pi/RaspberryPi-CM5/xgovenv/bin/python -u voice_interaction.py --mode stream --stream-window-seconds 10 --command-cooldown 0.8
```

## 7. 常用语音示例

任务：

```text
开始任务
去捡球
把红球送到门口
把绿球送到客厅
把蓝球送到门口
把黄球送到客厅
```

动作：

```text
坐下
握手
站起来
前进
后退
左转
右转
停止
```

问答：

```text
中国首都是哪里
杭州天气怎么样
最近的国际形势怎么样
今天世界杯比赛怎么样
阿根廷和佛得角比分怎么样
```

音乐：

```text
放歌
听歌
停歌
停止音乐
```

## 8. 测试文件

已加入或更新的测试包括：

```text
test_housekeeper_main.py
test_voice_interaction_chat_filter.py
test_voice_interaction_music.py
test_voice_interaction_tts.py
test_grab_then_follow_line.py
test_housekeeper_config.py
test_chat_config.py
```

常用本地验证：

```bash
python3 test_housekeeper_main.py
python3 test_voice_interaction_chat_filter.py
python3 test_voice_interaction_music.py
python3 test_voice_interaction_tts.py
python3 -m py_compile housekeeper_main.py voice_interaction.py web_dashboard.py echo_guard.py
```

## 9. 已知注意事项

- GitHub 仓库是公开仓库，不应提交真实 `DEEPSEEK_API_KEY`、讯飞密钥、Spark 密钥或机器狗 SSH 密码。
- `run_*.exp` 中的密码应使用本地占位符或本机私有脚本，不要把真实密码提交。
- DeepSeek API 不是原生联网搜索，本项目通过网页搜索上下文增强实时问题。
- 任务执行中默认不回答普通问题，避免机器狗边跑边播报导致噪音和误识别。
- 如果网页按钮停止仍不彻底，应优先检查是否有新的任务子进程名称没有加入 `TASK_STOP_PROCESS_PATTERNS`。
- 课程目录中的 `01.color_line/follow_line.py` 曾有无关换行差异，不属于智能管家犬功能本身。

## 10. 当前交接结论

本目录已经从单一课程 demo 演进为一个完整的智能管家犬总控模块。当前重点能力是：

- 主人认证后进入语音监听
- 多颜色抓球并送到目标站点
- DeepSeek 问答和实时网页搜索
- 天气直接走 DeepSeek/API，不再走本地天气指令
- 国际形势、世界杯、比分等实时问题会触发搜索增强
- 任务中静默和噪音过滤
- 网页仪表盘完整显示 ASR、回答和事件
- 网页按钮和语音停止共用强停止逻辑

后续如果继续迭代，建议优先做：

- 把网页停止按钮的执行结果在 UI 上显示更明确，例如显示实际杀掉的进程数量。
- 增加麦克风能量阈值或本地 VAD，进一步减少噪音输入。
- 为 DeepSeek 搜索结果增加来源标题和时间过滤。
- 把音乐播放器和总控做成 systemd 服务，掉电重启后自动恢复。
