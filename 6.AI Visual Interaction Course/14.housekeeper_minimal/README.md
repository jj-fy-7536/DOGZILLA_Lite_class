# 智能管家犬最小闭环

本目录存放当前智能管家犬的最小闭环代码，按阶段拆开，避免不同功能同时抢摄像头或同时控制机器狗。

## 当前入口

```text
14.housekeeper_minimal/
├── housekeeper_main.py          # 第一版总控: 人脸通过后听任务语音，再抓球巡线
├── run_housekeeper_main.exp     # 远端启动总控的 expect 脚本
├── face_interaction.py          # 阶段 1: 人脸检测、主人录入、主人/陌生人识别
├── face_interaction_summary.md  # 人脸模块参数与合并注意点
├── voice_interaction.py         # 语音控制、DeepSeek 问答、网页搜索、音乐
├── voice_interaction_handoff.md # 语音模块交接说明
├── find_and_align_line.py       # 抓球后寻找并对齐黑线
├── grab_then_follow_line.py     # 阶段 2/3: 抓球成功后对齐黑线并巡线
├── run_grab_then_follow_line.exp
├── echo_guard.py                # TTS 回声防护: 防止狗把自己的播报听成指令
├── expression_feedback.py       # LCD 表情反馈(PIL 直接绘制，不依赖表情图片)
├── frame_bus.py                 # 跨进程共享"最新一帧"(/dev/shm)，供仪表盘串流
└── web_dashboard.py             # 电脑端网页仪表盘: 阶段/表情/日志 + 全流程画面 + 停止任务
```

## 第一版总控

`housekeeper_main.py` 串联当前已经实测过的几个模块:

```text
人脸识别 owner -> 语音解析颜色/站点 -> 抓目标颜色球 -> 找线对齐 -> 巡线到二维码站点 -> 返航 home
```

启动脚本:

```bash
./run_housekeeper_main.exp
```

直接在机器狗上运行:

```bash
cd "/home/pi/DOGZILLA_Lite_class/6.AI Visual Interaction Course/14.housekeeper_minimal"
. /home/pi/dogzilla_runs/voice_env.sh
export PYTHONPATH=/home/pi/RaspberryPi-CM5/app:/home/pi/RaspberryPi-CM5/demos:.
/home/pi/RaspberryPi-CM5/xgovenv/bin/python -u housekeeper_main.py --robot-ip 172.20.10.9
```

触发任务的语音示例:

```text
开始任务
去捡球
帮我拿红球
开始抓球
把红球送到门口
把绿球送到门口
把蓝色球送到客厅
把黄色方块送到门口
```

结构化任务规则:

- 语音会解析目标颜色和目标站点，站点默认 `station_A`(客厅)。
- 当前抓取脚本支持 `red`、`green`、`blue`、`yellow` 四种 HSV 阈值；语音说红/绿/蓝/黄会透传到抓球脚本。
- 默认站点来自 `housekeeper_config.json`，内置 `home`、`station_A`、`station_B`。
- 目标站点通过巡线时识别二维码判断；读到非目标站点继续巡线，读到目标站点停车。
- 默认开启第一版返航:到目标站点后固定掉头，再以 `home` 为目标巡线，读到 `home` 后 `dog.reset()`。

运行中语音控制:

```text
停止 / 停下 / 急停
继续 / 恢复任务 / 接着执行（仅兼容旧暂停状态；现在停止会直接终止当前任务）
```

说明:

- `housekeeper_main.py` 启动后会常驻打开一个语音识别监听器，从人脸识别阶段开始一直监听到程序退出。
- 这个常驻监听器同时处理任务触发、停止、继续和 DeepSeek/Spark 简单问答，避免同时开两个 `arecord` 抢麦克风。
- 总控会在开始识别人脸、人脸识别成功、开始听语音等节点播报，同时在 LCD 上切换表情(scan/happy/listen/work/success/fail/pause)。默认 `--quiet-during-task` 开启，抓球/巡线任务执行中不语音回话，只在仪表盘记录。
- **回声防护**: 狗自己的 TTS 播报(如"开始执行捡球任务")被麦克风录到后不会再触发任务。EchoGuard 会把识别文本与最近 12 秒内播报过的内容做子串/相似度比对，命中则丢弃；与播报无关的话(如"停止")仍能穿透。
- 任务触发词收紧: 必须是明确短语(开始任务/去捡球等)，或"动词+球"同时出现(捡/拿/抓/找/送…+球)。单独说"球"或"眼球""进球"这类闲聊不再触发。
- 任务文本会显示为类似 `红球 -> 门口`、`绿球 -> 客厅`，同步写到仪表盘和控制台；默认任务中不 TTS 复述。
- 说"停止/停下/急停/暂停/别动"会终止当前抓球/巡线子进程、清空待执行任务、停止音乐并停狗；"别停""不要停"不会误触发急停。
- 停止后总控回到 LISTEN 等待新任务，不会自动断点续跑；需要继续干活就重新说任务。
- 停狗职责: 子进程收到 SIGINT 后自己停狗再退出(持串口方负责)；只有子进程被强杀时父进程才开串口兜底，且等 0.5 秒串口空闲后再发。
- 人脸认证为结构化握手: 总控给 `face_interaction.py` 传 `--exit-on-owner`，确认主人后子进程写 `auth_result.json` 并以 exit 0 自行退出(摄像头确定释放)，总控靠退出码+结果文件判断，不再 grep 日志。
- 调试时可加 `--no-voice-feedback` 只打印反馈，不进行 TTS 播报；`--no-quiet-during-task` 恢复任务过程播报；`--disable-voice-control` 禁用运行中的语音控制；`--no-expressions` 不画 LCD 表情；`--dashboard-port 0` 禁用仪表盘。
- 调试时可加 `--no-spark-chat` 关闭总控里的 AI 问答；问答优先读取 `DEEPSEEK_API_KEY`，没有配置时仍可回退旧的 `SPARK_API_PASSWORD`。
- 调试时可加 `--disable-return-home` 只跑到目标站点不返航；`--target-station station_B` 可覆盖语音解析出的站点；`--target-color green` 可覆盖语音解析出的颜色。

配置文件:

```text
14.housekeeper_minimal/housekeeper_config.json
```

关键字段:

```text
defaults.color              默认 red
defaults.target_station     默认 station_A
stations                    二维码内容、中文名称、语音别名
return_home                 是否返航、掉头速度/时间、返航超时
line.qr_decode_every_frames 二维码识别间隔帧数
line.result_path            巡线到站结果文件
```

分段调试:

```bash
# 只测语音解析和总控串联
/home/pi/RaspberryPi-CM5/xgovenv/bin/python -u housekeeper_main.py --dry-voice --disable-voice-control --no-voice-feedback

# 只测巡线到站，读到 station_B 后停车退出
cd "/home/pi/DOGZILLA_Lite_class/6.AI Visual Interaction Course/01.color_line"
/home/pi/RaspberryPi-CM5/xgovenv/bin/python -u follow_line.py --target-station station_B

# 跳过抓球/找线，直接测巡线到站和返航
cd "/home/pi/DOGZILLA_Lite_class/6.AI Visual Interaction Course/14.housekeeper_minimal"
/home/pi/RaspberryPi-CM5/xgovenv/bin/python -u grab_then_follow_line.py --skip-grab --skip-align --target-station station_B --return-home
```

## 电脑端仪表盘

总控启动后在电脑浏览器打开:

```text
http://172.20.10.9:8091/
```

- 顶部显示当前阶段(FACE_AUTH/LISTEN/TASK/DONE)、当前表情、任务文本(如 `红球 -> 门口`)。
- 顶部右侧“停止任务”按钮会终止当前任务、清空待执行任务、停止音乐并停狗；没有任务时点击不会让下一次任务卡在暂停状态。
- 中间是全流程实时画面: 人脸、抓球、找线、巡线各阶段子进程通过 `frame_bus`(/dev/shm 共享文件)发布最新一帧，仪表盘统一串流，不用再换端口。
- 右侧是播报与事件日志，会区分“我听到”“机器狗说”“执行”“忽略”，方便看清 ASR 到底识别了什么。
- 原有的分阶段串流仍然可用: 人脸 8090、巡线调试 8080。

## 语音指令覆盖

`voice_interaction.py` 独立语音模块目前明确覆盖动作、音乐、问答等能力；天气不再是单独内置指令，会作为普通问句直接进入 DeepSeek/API。

明确功能类别:

```text
停止、坐下、握手、站起来、音乐播放/停止、你是谁/自我介绍、你会什么/能力说明、报时、前进、后退、左转、右转
```

除此之外，问答/请求会走 DeepSeek；天气、国际形势、新闻、比分等实时问题会先抓网页搜索上下文再交给 DeepSeek。如果没有配置 DeepSeek，也可以继续走旧 Spark Lite:

```text
27 个问句关键词 + 24 个请求关键词 + 6 个唤醒词
```

`housekeeper_main.py` 总控额外识别:

```text
任务触发: 开始任务、执行任务、开始工作、去捡球、捡球、抓球、拿球、拿红球、找球、送球，
         以及"动词+球/方块/物品"组合句(捡/拣/见/拿/抓/找/送/取/提/带 + 目标物，含常见同音误识别)
槽位解析: 红/绿/蓝/黄 + 客厅/station_A、门口/station_B、家/home
运行控制: 停止/停下/急停/暂停/别动(带"别停/不要停"负向排除)会立即终止当前任务；继续/恢复任务/接着执行仅兼容旧暂停状态
音乐控制: 放歌/播放音乐/听歌 -> 调 `/home/pi/dogzilla_runs/dogzilla_music_player.py --background`；停歌/停止播放 -> 调 `--stop`
非任务问句/请求: 复用 `voice_interaction.py` 的问答过滤和 DeepSeek/Spark HTTP 调用；明显噪音/碎片词不会进入 AI
```

## 人脸识别模块

运行位置: 机器狗 Raspberry Pi CM5

```bash
cd "/home/pi/DOGZILLA_Lite_class/6.AI Visual Interaction Course/14.housekeeper_minimal"
/home/pi/RaspberryPi-CM5/xgovenv/bin/python -u face_interaction.py --robot-ip 172.20.10.9
```

电脑浏览器访问:

```text
http://172.20.10.9:8090/
```

人脸库默认保存到:

```text
/home/pi/RaspberryPi-CM5/car/faces/owner/
```

## 语音控制模块

`voice_interaction.py` 通过麦克风持续监听语音，可执行坐下、握手、站起来、停止、前进、后退、左转、右转，也支持音乐播放、天气问答、新闻/国际形势问答和简单问答。

密钥文件不要放进本目录。机器狗上仍建议把讯飞、在线语音合成和 AI 问答密钥放在:

```text
/home/pi/dogzilla_runs/voice_env.sh
```

在线语音合成默认使用讯飞 WebAPI 发音人 `x4_xiaoyan`，比旧的 `xgoedu.SpeechSynthesis()` 更接近真人。可以通过环境变量覆盖:

```bash
export XFYUN_TTS_APPID="你的在线语音合成APPID"
export XFYUN_TTS_API_KEY="你的在线语音合成APIKey"
export XFYUN_TTS_API_SECRET="你的在线语音合成APISecret"
export XFYUN_TTS_VCN="x4_xiaoyan"
```

AI 问答默认优先使用 DeepSeek:

```bash
export DEEPSEEK_API_KEY="YOUR_DEEPSEEK_API_KEY"
export DEEPSEEK_MODEL="deepseek-v4-flash"
# 可选；默认就是这个地址
export DEEPSEEK_API_URL="https://api.deepseek.com/chat/completions"
```

旧 Spark 配置仍保留兼容；只有没有 `DEEPSEEK_API_KEY` 时才会读取:

```bash
export SPARK_API_PASSWORD="你的Spark Lite APIPassword"
export SPARK_MODEL="lite"
```

音乐播放使用机器狗上的脚本和歌曲目录:

```text
/home/pi/dogzilla_runs/dogzilla_music_player.py
/home/pi/dogzilla_runs/music/
```

示例启动:

```bash
cd "/home/pi/DOGZILLA_Lite_class/6.AI Visual Interaction Course/14.housekeeper_minimal"
. /home/pi/dogzilla_runs/voice_env.sh
export PYTHONPATH=/home/pi/RaspberryPi-CM5/app:/home/pi/RaspberryPi-CM5/demos:.
/home/pi/RaspberryPi-CM5/xgovenv/bin/python -u voice_interaction.py --mode stream --stream-window-seconds 10 --command-cooldown 0.8
```

调试命令解析但不控制机器狗:

```bash
/home/pi/RaspberryPi-CM5/xgovenv/bin/python -u voice_interaction.py --dry-run
```

## 合并注意

- 人脸模块、抓球模块、巡线模块都会用摄像头，正式总控里不能同时创建多个 `Picamera2()`。
- `face_interaction.py` 目前是独立运行模块，会自己打开摄像头和网页端口 `8090`。
- `voice_interaction.py` 不使用摄像头，但会直接控制机器狗动作，并占用麦克风录音。
- 音乐播放依赖机器狗上的 `/home/pi/dogzilla_runs/dogzilla_music_player.py` 和 `/home/pi/dogzilla_runs/music/`，默认后台播放目录第一首歌。
- 语音识别需要 `XFYUN_APPID`、`XFYUN_API_KEY`、`XFYUN_API_SECRET`；在线合成可单独配置 `XFYUN_TTS_APPID`、`XFYUN_TTS_API_KEY`、`XFYUN_TTS_API_SECRET`；问答优先需要 `DEEPSEEK_API_KEY`，旧 Spark 回退需要 `SPARK_API_PASSWORD`。
- `grab_then_follow_line.py` 是当前抓球后巡线入口，仍保持独立启动。
- 后续总控应抽出共享摄像头，再把人脸识别改成接收外部 frame。
