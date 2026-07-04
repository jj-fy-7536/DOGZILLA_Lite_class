# DOGZILLA Lite 语音控制模块交接说明

## 文件

- 主程序：`voice_interaction.py`
- 机器狗运行目录：`/home/pi/dogzilla_runs/`
- 机器狗上的主程序路径：`/home/pi/dogzilla_runs/voice_interaction.py`
- 机器狗上的密钥环境文件：`/home/pi/dogzilla_runs/voice_env.sh`

注意：`voice_env.sh` 里面放讯飞和星火密钥，不要上传 GitHub，不要发到群里。

## 功能

这个模块让 DOGZILLA Lite 通过麦克风持续监听语音，并执行以下能力：

- 动作控制：坐下、握手、站起来、停止、前进、后退、左转、右转
- 天气查询：必须带城市，例如“绍兴天气怎么样”“今天绍兴冷不冷”
- 音乐播放：识别“放歌”“播放音乐”“听歌”后播放 `/home/pi/dogzilla_runs/music/` 里的歌曲；“停歌”“停止播放”停止音乐
- 简单智能问答：例如“中国首都是哪里”“你是谁”
- 请求类语句：例如“背一下静夜思”“讲个故事”“介绍一下李白”

普通陈述句不会触发 AI，例如“今天我去上课”“我放了个东西”会被忽略，避免机器狗乱回答。

## 运行环境

机器狗需要：

- DOGZILLA Lite 官方 Python 环境
- `/home/pi/RaspberryPi-CM5/xgovenv/bin/python`
- 官方库：`xgolib`、`xgoedu`
- 麦克风录音命令：`arecord`
- 播放命令：`mplayer`
- Python 包：`websocket-client`
- 能访问互联网，用于讯飞语音识别、星火大模型、天气查询

## 密钥配置

在机器狗上创建或检查：

```bash
mkdir -p /home/pi/dogzilla_runs
nano /home/pi/dogzilla_runs/voice_env.sh
```

文件内容模板：

```bash
export XFYUN_APPID="你的讯飞APPID"
export XFYUN_API_KEY="你的讯飞APIKey"
export XFYUN_API_SECRET="你的讯飞APISecret"
export XFYUN_TTS_APPID="你的在线语音合成APPID"
export XFYUN_TTS_API_KEY="你的在线语音合成APIKey"
export XFYUN_TTS_API_SECRET="你的在线语音合成APISecret"
export XFYUN_TTS_VCN="x4_xiaoyan"
export SPARK_API_PASSWORD="你的Spark Lite APIPassword"
export SPARK_MODEL="lite"
```

保存后执行：

```bash
chmod 600 /home/pi/dogzilla_runs/voice_env.sh
```

## 上传程序

在电脑上把 `voice_interaction.py` 上传到机器狗：

```bash
scp voice_interaction.py pi@172.20.10.4:/home/pi/dogzilla_runs/voice_interaction.py
```

如果机器狗 IP 变了，把 `172.20.10.4` 换成实际 IP。

## 启动语音模块

SSH 进入机器狗后运行：

```bash
cd /home/pi/dogzilla_runs
. ./voice_env.sh
export PYTHONPATH=/home/pi/RaspberryPi-CM5/app:/home/pi/RaspberryPi-CM5/demos:.
/home/pi/RaspberryPi-CM5/xgovenv/bin/python voice_interaction.py --mode stream --stream-window-seconds 10 --command-cooldown 0.8
```

后台启动方式：

```bash
cd /home/pi/dogzilla_runs
: > voice_interaction.log
. ./voice_env.sh
export PYTHONPATH=/home/pi/RaspberryPi-CM5/app:/home/pi/RaspberryPi-CM5/demos:.
nohup /home/pi/RaspberryPi-CM5/xgovenv/bin/python voice_interaction.py --mode stream --stream-window-seconds 10 --command-cooldown 0.8 >> voice_interaction.log 2>&1 < /dev/null &
echo $! > voice_interaction.pid
```

查看日志：

```bash
tail -f /home/pi/dogzilla_runs/voice_interaction.log
```

## 关闭语音模块

```bash
PID=$(cat /home/pi/dogzilla_runs/voice_interaction.pid 2>/dev/null)
if [ -n "$PID" ]; then kill "$PID"; fi
```

如果 PID 文件失效，可以手动查杀：

```bash
ps aux | grep voice_interaction.py
kill 进程号
```

停止机器狗运动：

```bash
PYTHONPATH=/home/pi/RaspberryPi-CM5/app:/home/pi/RaspberryPi-CM5/demos:. /home/pi/RaspberryPi-CM5/xgovenv/bin/python - <<'PY'
import sys, time
sys.path[:0] = ['/home/pi/RaspberryPi-CM5/app', '/home/pi/RaspberryPi-CM5/demos']
from xgolib import XGO
try:
    dog = XGO(port='/dev/ttyAMA0', version='xgolite')
except TypeError:
    dog = XGO('xgolite')
for _ in range(4):
    dog.move('x', 0)
    dog.move('y', 0)
    dog.turn(0)
    dog.stop()
    time.sleep(0.04)
PY
```

## 可用语音示例

动作：

- “坐下”
- “握手”
- “站起来”
- “停止”
- “前进”
- “后退”
- “左转”
- “右转”

音乐：

- “放歌”
- “播放音乐”
- “听歌”
- “停歌”

天气：

- “绍兴天气怎么样”
- “杭州今天热不热”
- “北京会不会下雨”

注意：问天气必须说城市。只说“今天天气怎么样”时，机器狗会问你想查哪个城市。

智能问答和请求：

- “你是谁”
- “你会什么”
- “中国首都是哪里”
- “背一下静夜思”
- “讲个故事”
- “介绍一下李白”

## 当前逻辑重点

- 语音识别使用讯飞 IAT WebSocket，默认实时流模式。
- 语音播报优先使用讯飞在线语音合成 WebAPI，默认发音人 `x4_xiaoyan`；失败时回退到官方 `SpeechSynthesis()`。
- 音乐播放调用 `/home/pi/dogzilla_runs/dogzilla_music_player.py --background`，默认播放 `/home/pi/dogzilla_runs/music/` 的第一首歌。
- 大模型使用 Spark Lite HTTP 接口。
- 明确动作指令优先，不会被大模型抢走。
- 普通陈述句默认忽略，只有问句或明显对机器狗发出的请求才会进入大模型。
- 机器狗播报后会短时间过滤回声，避免它听到自己的声音后重复回答。
- 在线语音合成会生成 16k 单声道 WAV 并用 `aplay`/`mplayer` 播放；回退到 `SpeechSynthesis()` 时仍不会额外播放第二遍。

## 常见问题

没有反应：

- 检查机器狗是否联网。
- 检查 `voice_interaction.py` 是否在运行。
- 查看 `/home/pi/dogzilla_runs/voice_interaction.log`。
- 检查 `voice_env.sh` 是否配置了讯飞和 Spark 密钥。

动作不执行：

- 确认不是只启动在电脑上，程序必须在机器狗上运行。
- 确认 `PYTHONPATH` 包含 `/home/pi/RaspberryPi-CM5/app:/home/pi/RaspberryPi-CM5/demos:.`。

天气城市不对：

- 说完整城市名，例如“绍兴天气怎么样”，不要只说“今天天气怎么样”。
- 如果城市名识别错，先看日志里的 `[ASR]` 内容。

大模型不回答：

- 普通陈述句会被故意忽略。
- 尝试说成请求句或问句，例如“背一下静夜思”“这个问题怎么解决”。
- 检查 `SPARK_API_PASSWORD` 是否可用。
