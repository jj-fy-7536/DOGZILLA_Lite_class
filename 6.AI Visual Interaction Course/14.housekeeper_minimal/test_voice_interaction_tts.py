import base64
import importlib.util
import sys
import tempfile
import unittest
import wave
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

MODULE_PATH = Path(__file__).with_name("voice_interaction.py")
SPEC = importlib.util.spec_from_file_location("voice_interaction", MODULE_PATH)
voice_interaction = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = voice_interaction
SPEC.loader.exec_module(voice_interaction)


class XunfeiTtsTests(unittest.TestCase):
    def test_payload_uses_human_voice_and_utf8_text(self):
        tts = voice_interaction.XunfeiTTS(
            "appid",
            "api-key",
            "api-secret",
            vcn="x4_xiaoyan",
            speed=48,
            volume=70,
            pitch=52,
        )

        payload = tts.build_request_payload("你好，我是机器狗")

        self.assertEqual(payload["common"]["app_id"], "appid")
        self.assertEqual(payload["business"]["vcn"], "x4_xiaoyan")
        self.assertEqual(payload["business"]["aue"], "raw")
        self.assertEqual(payload["business"]["auf"], "audio/L16;rate=16000")
        self.assertEqual(payload["business"]["tte"], "UTF8")
        self.assertEqual(payload["business"]["speed"], 48)
        self.assertEqual(payload["business"]["volume"], 70)
        self.assertEqual(payload["business"]["pitch"], 52)
        self.assertEqual(payload["data"]["status"], 2)
        self.assertEqual(
            base64.b64decode(payload["data"]["text"]).decode("utf-8"),
            "你好，我是机器狗",
        )

    def test_save_pcm_as_wav_writes_16k_mono_file(self):
        tts = voice_interaction.XunfeiTTS("appid", "api-key", "api-secret")

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "tts.wav"
            tts.save_pcm_as_wav(b"\x00\x00\x01\x00", output)

            with wave.open(str(output), "rb") as wav:
                self.assertEqual(wav.getnchannels(), 1)
                self.assertEqual(wav.getsampwidth(), 2)
                self.assertEqual(wav.getframerate(), 16000)
                self.assertEqual(wav.readframes(2), b"\x00\x00\x01\x00")


if __name__ == "__main__":
    unittest.main()
