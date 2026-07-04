"""LCD 表情反馈。

不依赖 /home/pi/xgoPictures 下的表情图片(部分机器上不全),
直接用 PIL 在 2 寸 LCD 上画简单表情。硬件不可用时(比如在电脑上调试)
自动降级为控制台打印,不影响主流程。

表情只在状态切换时画一帧,不做持续动画,避免和抓球/巡线子进程
持续刷新的 LCD 画面互相打架。
"""

from __future__ import annotations

SCREEN_W = 320
SCREEN_H = 240

# 眼睛基准位置
_EYE_Y = 100
_EYE_LX = 100
_EYE_RX = 220
_EYE_R = 38

EXPRESSIONS = ("sleep", "scan", "happy", "listen", "work", "success", "fail", "pause")


class ExpressionDisplay:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.current = ""
        self._display = None
        self._failed = False

    def show(self, name: str) -> None:
        """画一个表情;name 不在列表里则忽略。重复表情不重画。"""
        if name not in EXPRESSIONS or name == self.current:
            return
        self.current = name
        print("[FACE] {}".format(name), flush=True)
        if not self.enabled or self._failed:
            return
        try:
            image = self._render(name)
            self._ensure_display()
            self._display.ShowImage(image)
        except Exception as exc:
            # 屏幕初始化失败只报一次,之后静默
            self._failed = True
            print("[FACE] lcd unavailable: {!r}".format(exc), flush=True)

    def _ensure_display(self):
        if self._display is None:
            import xgoscreen.LCD_2inch as LCD_2inch

            self._display = LCD_2inch.LCD_2inch()
            self._display.clear()
        return self._display

    def _render(self, name: str):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (SCREEN_W, SCREEN_H), "black")
        draw = ImageDraw.Draw(image)
        color = (80, 220, 255)
        renderers = {
            "sleep": self._draw_sleep,
            "scan": self._draw_scan,
            "happy": self._draw_happy,
            "listen": self._draw_listen,
            "work": self._draw_work,
            "success": self._draw_success,
            "fail": self._draw_fail,
            "pause": self._draw_pause,
        }
        renderers[name](draw, color)
        return image

    @staticmethod
    def _eye_box(cx: int, cy: int, r: int) -> tuple[int, int, int, int]:
        return (cx - r, cy - r, cx + r, cy + r)

    def _draw_sleep(self, draw, color) -> None:
        for cx in (_EYE_LX, _EYE_RX):
            draw.line((cx - _EYE_R, _EYE_Y, cx + _EYE_R, _EYE_Y), fill=color, width=10)
        draw.text((250, 30), "z", fill=color)
        draw.text((270, 12), "Z", fill=color)

    def _draw_scan(self, draw, color) -> None:
        for cx in (_EYE_LX, _EYE_RX):
            draw.ellipse(self._eye_box(cx, _EYE_Y, _EYE_R), outline=color, width=8)
            draw.ellipse(self._eye_box(cx + 10, _EYE_Y, 8), fill=color)

    def _draw_happy(self, draw, color) -> None:
        for cx in (_EYE_LX, _EYE_RX):
            draw.arc(self._eye_box(cx, _EYE_Y + 14, _EYE_R), 200, 340, fill=color, width=10)
        draw.arc((130, 150, 190, 190), 20, 160, fill=color, width=8)

    def _draw_listen(self, draw, color) -> None:
        for cx in (_EYE_LX, _EYE_RX):
            draw.ellipse(self._eye_box(cx, _EYE_Y, _EYE_R), fill=color)
        draw.ellipse((150, 165, 170, 185), outline=color, width=6)

    def _draw_work(self, draw, color) -> None:
        for cx in (_EYE_LX, _EYE_RX):
            draw.ellipse(self._eye_box(cx, _EYE_Y, _EYE_R), fill=color)
            draw.rectangle((cx - _EYE_R, _EYE_Y - _EYE_R, cx + _EYE_R, _EYE_Y - 8), fill="black")

    def _draw_success(self, draw, color) -> None:
        for cx in (_EYE_LX, _EYE_RX):
            draw.line((cx - _EYE_R, _EYE_Y, cx, _EYE_Y - _EYE_R + 6), fill=color, width=10)
            draw.line((cx, _EYE_Y - _EYE_R + 6, cx + _EYE_R, _EYE_Y), fill=color, width=10)
        draw.arc((120, 140, 200, 200), 20, 160, fill=color, width=8)

    def _draw_fail(self, draw, color) -> None:
        for cx in (_EYE_LX, _EYE_RX):
            draw.line((cx - _EYE_R, _EYE_Y - 14, cx + _EYE_R, _EYE_Y + 14), fill=color, width=10)
            draw.line((cx - _EYE_R, _EYE_Y + 14, cx + _EYE_R, _EYE_Y - 14), fill=color, width=10)
        draw.arc((130, 170, 190, 210), 200, 340, fill=color, width=6)

    def _draw_pause(self, draw, color) -> None:
        for cx in (_EYE_LX, _EYE_RX):
            draw.rectangle((cx - 24, _EYE_Y - _EYE_R, cx - 6, _EYE_Y + _EYE_R), fill=color)
            draw.rectangle((cx + 6, _EYE_Y - _EYE_R, cx + 24, _EYE_Y + _EYE_R), fill=color)
