"""TTS 回声防护。

机器狗播报的话("开始执行捡球任务"等)会被自己的麦克风录到、再被 ASR 识别回来,
其中包含"捡球""抓球"等触发词,会让总控自己触发任务。

EchoGuard 记录最近播报的文本和时间,语音监听器拿到识别结果后先问一句
is_echo(),命中则丢弃。判断规则:识别文本与"正在播报或刚播报完(记忆窗口内)"
的文本互为子串、或模糊相似度达到阈值。
"""

from __future__ import annotations

import re
import threading
import time
from difflib import SequenceMatcher


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?;；:：\"'“”‘’]", "", text.strip().lower())


class EchoGuard:
    def __init__(
        self,
        memory_seconds: float = 12.0,
        similarity_threshold: float = 0.6,
        clock=time.time,
    ) -> None:
        self.memory_seconds = memory_seconds
        self.similarity_threshold = similarity_threshold
        self._clock = clock
        self._lock = threading.Lock()
        # 每条记录: (normalized_text, end_time);还没播完的 end_time 为 None
        self._recent: list[list] = []

    def begin_speaking(self, text: str) -> None:
        normalized = _normalize(text)
        if not normalized:
            return
        with self._lock:
            self._recent.append([normalized, None])
            self._prune_locked()

    def end_speaking(self, text: str) -> None:
        normalized = _normalize(text)
        now = self._clock()
        with self._lock:
            for entry in reversed(self._recent):
                if entry[0] == normalized and entry[1] is None:
                    entry[1] = now
                    break
            self._prune_locked()

    def is_echo(self, recognized: str) -> bool:
        candidate = _normalize(recognized)
        if not candidate:
            return False
        now = self._clock()
        with self._lock:
            self._prune_locked()
            for spoken, end_time in self._recent:
                if end_time is not None and now - end_time > self.memory_seconds:
                    continue
                if self._matches(candidate, spoken):
                    return True
        return False

    def _matches(self, candidate: str, spoken: str) -> bool:
        if len(candidate) >= 2 and (candidate in spoken or spoken in candidate):
            return True
        return SequenceMatcher(None, candidate, spoken).ratio() >= self.similarity_threshold

    def _prune_locked(self) -> None:
        now = self._clock()
        self._recent = [
            entry
            for entry in self._recent
            if entry[1] is None or now - entry[1] <= self.memory_seconds * 2
        ][-20:]
