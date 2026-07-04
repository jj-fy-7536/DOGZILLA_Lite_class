#!/usr/bin/env python3
"""语音控制：说「坐下」让机器狗执行坐下动作。"""

import base64
import hashlib
import hmac
import json
import os
import ssl
import time
import _thread as thread
from datetime import datetime
from time import mktime
from urllib.parse import urlencode
from wsgiref.handlers import format_date_time

import websocket
from xgoedu import XGOEDU
from xgolib import XGO

# 讯飞语音识别密钥，可在环境变量中覆盖
XINGHOU_APPID = os.environ.get("XINGHOU_APPID", "b03a849b")
XINGHOU_APISecret = os.environ.get("XINGHOU_APISecret", "M2Q3Y2I5MDhmZjljNThlYTYyNWVhYTYw")
XINGHOU_KEY = os.environ.get("XINGHOU_KEY", "d4d35cc1ac65cd6879216527289bf44a")

AUDIO_FILE = "/home/pi/xgoMusic/myrecord.wav"
RECORD_SECONDS = 4
SIT_ACTION = 1  # 预设动作：坐下

STATUS_FIRST_FRAME = 0
STATUS_CONTINUE_FRAME = 1
STATUS_LAST_FRAME = 2

_recognized_text = ""
_ws_param = None


class WsParam:
    def __init__(self, app_id, api_key, api_secret, audio_file):
        self.APPID = app_id
        self.APIKey = api_key
        self.APISecret = api_secret
        self.AudioFile = audio_file
        self.CommonArgs = {"app_id": self.APPID}
        self.BusinessArgs = {
            "domain": "iat",
            "language": "zh_cn",
            "accent": "mandarin",
            "vinfo": 1,
            "vad_eos": 10000,
        }

    def create_url(self):
        url = "wss://ws-api.xfyun.cn/v2/iat"
        now = datetime.now()
        date = format_date_time(mktime(now.timetuple()))
        signature_origin = (
            "host: ws-api.xfyun.cn\n"
            f"date: {date}\n"
            "GET /v2/iat HTTP/1.1"
        )
        signature_sha = hmac.new(
            self.APISecret.encode("utf-8"),
            signature_origin.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        signature_sha = base64.b64encode(signature_sha).decode(encoding="utf-8")
        authorization_origin = (
            f'api_key="{self.APIKey}", algorithm="hmac-sha256", '
            f'headers="host date request-line", signature="{signature_sha}"'
        )
        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode(
            encoding="utf-8"
        )
        query = urlencode(
            {"authorization": authorization, "date": date, "host": "ws-api.xfyun.cn"}
        )
        return url + "?" + query


def _on_message(ws, message):
    global _recognized_text
    try:
        payload = json.loads(message)
        if payload.get("code") != 0:
            return
        for item in payload["data"]["result"]["ws"]:
            for word in item["cw"]:
                _recognized_text += word["w"]
    except Exception as exc:
        print("解析识别结果失败:", exc)


def _on_error(ws, error):
    print("语音识别错误:", error)


def _on_close(ws, *args):
    pass


def _on_open(ws):
    def run(*args):
        frame_size = 8000
        interval = 0.04
        status = STATUS_FIRST_FRAME

        with open(_ws_param.AudioFile, "rb") as fp:
            while True:
                buf = fp.read(frame_size)
                if not buf:
                    status = STATUS_LAST_FRAME

                if status == STATUS_FIRST_FRAME:
                    ws.send(
                        json.dumps(
                            {
                                "common": _ws_param.CommonArgs,
                                "business": _ws_param.BusinessArgs,
                                "data": {
                                    "status": 0,
                                    "format": "audio/L16;rate=16000",
                                    "audio": str(base64.b64encode(buf), "utf-8"),
                                    "encoding": "raw",
                                },
                            }
                        )
                    )
                    status = STATUS_CONTINUE_FRAME
                elif status == STATUS_CONTINUE_FRAME:
                    ws.send(
                        json.dumps(
                            {
                                "data": {
                                    "status": 1,
                                    "format": "audio/L16;rate=16000",
                                    "audio": str(base64.b64encode(buf), "utf-8"),
                                    "encoding": "raw",
                                }
                            }
                        )
                    )
                elif status == STATUS_LAST_FRAME:
                    ws.send(
                        json.dumps(
                            {
                                "data": {
                                    "status": 2,
                                    "format": "audio/L16;rate=16000",
                                    "audio": str(base64.b64encode(buf), "utf-8"),
                                    "encoding": "raw",
                                }
                            }
                        )
                    )
                    time.sleep(1)
                    break
                time.sleep(interval)
        ws.close()

    thread.start_new_thread(run, ())


def recognize_speech(audio_file):
    global _recognized_text, _ws_param
    _recognized_text = ""
    _ws_param = WsParam(XINGHOU_APPID, XINGHOU_KEY, XINGHOU_APISecret, audio_file)
    websocket.enableTrace(False)
    ws = websocket.WebSocketApp(
        _ws_param.create_url(),
        on_message=_on_message,
        on_error=_on_error,
        on_close=_on_close,
    )
    ws.on_open = _on_open
    ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
    return _recognized_text.strip()


def normalize_text(text):
    for ch in "。，！？、；：""''":
        text = text.replace(ch, "")
    return text.replace(" ", "")


def is_sit_command(text):
    text = normalize_text(text)
    return "坐下" in text or text == "坐"


def main():
    edu = XGOEDU()
    dog = XGO(port="/dev/ttyAMA0", version="xgolite")

    print("语音控制：说「坐下」让机器狗坐下")
    print("按 Ctrl+C 退出\n")

    try:
        while True:
            print(f"正在录音 {RECORD_SECONDS} 秒，请说「坐下」...")
            edu.xgoAudioRecord(filename="myrecord", seconds=RECORD_SECONDS)
            time.sleep(0.5)

            text = recognize_speech(AUDIO_FILE)
            print(f"识别结果: {text or '(空)'}")

            if is_sit_command(text):
                print("执行：坐下")
                dog.action(SIT_ACTION)
                time.sleep(2)
            else:
                print("未识别到「坐下」，请再试一次\n")

    except KeyboardInterrupt:
        print("\n退出")
    finally:
        dog.reset()


if __name__ == "__main__":
    main()
