from dataclasses import dataclass

import numpy as np


IMAGE_CENTER_X = 160
STRAIGHT_SPEED = 10
CURVE_SPEED = 7
CORNER_SPEED = 5
CURVE_TURN_MULTIPLIER = 1.4
CURVE_TURN_LIMIT = 32
CORNER_TURN_MULTIPLIER = 2.0
CORNER_TURN_LIMIT = 28

MIN_SEGMENT_WIDTH = 6
CORNER_MIN_RUN_WIDTH = 90
CORNER_MIN_OFFSET = 35
BOTTOM_BANDS = ((190, 239), (155, 205), (115, 165))
BOTTOM_BAND_WEIGHTS = (3, 2, 1)
CURVE_MIN_SPREAD = 45
CORNER_Y_RANGE = (20, 125)

# 转向锁定：识别到直角后锁定若干帧持续转向，避免被底部边缘干扰抢回直线模式
# Corner lock: keep turning for several frames after a corner is detected,
# so bottom-edge noise cannot pull the target back to "straight" mid-turn.
CORNER_LOCK_FRAMES = 12
CORNER_LOCK_EXIT_STABLE_FRAMES = 3
CORNER_LOCK_EXIT_CENTER_TOL = 45
CORNER_LOCK_TARGET_OFFSET = 100
CORNER_LOCK_TURN = 45

# 阴影抑制：贴着画面左右边缘的色块和过宽的大色块按阴影处理，直接不参与选线
# Shadow rejection: blobs hugging the left/right frame edge, or blobs far wider
# than a real tape line, are treated as shadows and never selected.
EDGE_REJECT_ZONE = 25
MAX_LINE_WIDTH = 130

# 直线模式抗干扰：目标点单帧跳变超过该值则忽略；连续多帧被忽略后重新接受，防止卡死
# Straight-mode noise rejection: ignore targets jumping too far in one frame,
# but re-acquire after enough consecutive rejections so the dog never stalls forever.
MAX_STRAIGHT_JUMP = 70
MAX_CURVE_JUMP = 110
REACQUIRE_AFTER_REJECTS = 10

# 丢线搜索：连续丢失若干帧后原地旋转找线，先朝最后看到线的一侧扫，再反向扫
# Lost-line search: after several lost frames, rotate in place to reacquire the
# line, sweeping toward the last-seen side first and then the opposite side.
LOST_FRAMES_BEFORE_SEARCH = 3
SEARCH_SWEEP_FRAMES = 30
SEARCH_TURN = 35


@dataclass
class LineDecision:
    found: bool
    x: int = IMAGE_CENTER_X
    y: int = 0
    mode: str = "not_found"
    speed: int = 0
    turn_multiplier: float = 1.0
    turn_limit: int = 150
    turn_override: float = None  # 不为None时跳过PID，直接用该值转向 Skip PID and turn with this value when set


class LineTracker:
    """带状态的巡线决策：转向锁定 + 丢线搜索 Stateful decision: corner lock + lost-line search."""

    def __init__(self):
        self._lock_mode = None  # "corner_right" / "corner_left"
        self._lock_frames_left = 0
        self._stable_exit_count = 0
        self._reject_count = 0
        self._lost_count = 0
        self._last_seen_side = 1  # 1=右 right, -1=左 left
        self._search_side = 0
        self._search_frames_left = 0
        self._search_exhausted = False

    def decide(self, binary, last_x=IMAGE_CENTER_X):
        mask = np.asarray(binary) > 0
        if mask.ndim != 2 or mask.size == 0:
            return LineDecision(False)

        if self._lock_frames_left > 0:
            decision = self._locked_decision(mask, last_x)
        elif self._search_frames_left > 0:
            decision = self._search_decision(mask)
        else:
            decision = self._normal_decision(mask, last_x)

        if decision.found and not decision.mode.startswith("search"):
            self._lost_count = 0
            self._search_exhausted = False
            if decision.x >= IMAGE_CENTER_X + 20:
                self._last_seen_side = 1
            elif decision.x <= IMAGE_CENTER_X - 20:
                self._last_seen_side = -1
        return decision

    # ---------- 普通巡线 normal tracking ----------

    def _normal_decision(self, mask, last_x):
        corner = _detect_corner_branch(mask, last_x)
        if corner is not None:
            self._engage_lock(corner.mode)
            return corner

        straight = self._straight_with_jump_filter(mask, last_x)
        if straight is not None:
            return straight

        # 真正丢线：累计到阈值后进入搜索 Truly lost: start searching after enough frames
        if not self._search_exhausted:
            self._lost_count += 1
            if self._lost_count >= LOST_FRAMES_BEFORE_SEARCH:
                self._begin_search(self._last_seen_side)
                return self._search_decision(mask)
        return LineDecision(False)

    def _straight_with_jump_filter(self, mask, last_x):
        near = _detect_bottom_line(mask, last_x, max_jump=MAX_STRAIGHT_JUMP)
        if near is not None:
            self._reject_count = 0
            return near

        far = _detect_bottom_line(mask, last_x)
        if far is None:
            self._reject_count = 0
            return None

        # 目标跳变太大，先忽略；连续多帧后重新接受，避免永久停住
        # Too big a jump: reject for now, re-acquire after enough consecutive rejects
        self._reject_count += 1
        if self._reject_count >= REACQUIRE_AFTER_REJECTS:
            self._reject_count = 0
            return far
        return LineDecision(False)  # 有远处目标但暂不采纳，也不触发搜索 Seen but not adopted; no search

    # ---------- 转向锁定 corner lock ----------

    def _engage_lock(self, mode):
        self._lock_mode = mode
        self._lock_frames_left = CORNER_LOCK_FRAMES
        self._stable_exit_count = 0
        self._end_search()

    def _locked_decision(self, mask, last_x):
        self._lock_frames_left -= 1

        # 弯角仍然可见则刷新锁定，支撑更大的弯 Refresh lock while the corner stays visible
        refreshed = _detect_corner_branch(mask, last_x)
        if refreshed is not None and refreshed.mode == self._lock_mode:
            self._lock_frames_left = CORNER_LOCK_FRAMES

        # 只有底部重新出现靠近中心的稳定线才提前解锁
        # Release early only when a stable bottom line reappears near image center
        straight = _detect_bottom_line(mask, last_x)
        if straight is not None and abs(straight.x - IMAGE_CENTER_X) <= CORNER_LOCK_EXIT_CENTER_TOL:
            self._stable_exit_count += 1
            if self._stable_exit_count >= CORNER_LOCK_EXIT_STABLE_FRAMES:
                self._release_lock()
                return straight
        else:
            self._stable_exit_count = 0

        direction = self._lock_mode
        if self._lock_frames_left <= 0:
            # 锁定耗尽仍没接上线：转入搜索，继续朝弯的方向找
            # Lock expired without reacquiring: hand over to search, same direction
            side = 1 if direction == "corner_right" else -1
            self._release_lock()
            self._begin_search(side)

        if direction == "corner_right":
            return LineDecision(
                True,
                x=IMAGE_CENTER_X + CORNER_LOCK_TARGET_OFFSET,
                y=0,
                mode="corner_lock_right",
                speed=CORNER_SPEED,
                turn_multiplier=CORNER_TURN_MULTIPLIER,
                turn_limit=CORNER_LOCK_TURN,
                turn_override=-CORNER_LOCK_TURN,
            )
        return LineDecision(
            True,
            x=IMAGE_CENTER_X - CORNER_LOCK_TARGET_OFFSET,
            y=0,
            mode="corner_lock_left",
            speed=CORNER_SPEED,
            turn_multiplier=CORNER_TURN_MULTIPLIER,
            turn_limit=CORNER_LOCK_TURN,
            turn_override=CORNER_LOCK_TURN,
        )

    def _release_lock(self):
        self._lock_mode = None
        self._lock_frames_left = 0
        self._stable_exit_count = 0

    # ---------- 丢线搜索 lost-line search ----------

    def _begin_search(self, side):
        self._search_side = side if side in (1, -1) else 1
        self._search_frames_left = SEARCH_SWEEP_FRAMES * 3
        self._lost_count = 0

    def _end_search(self):
        self._search_side = 0
        self._search_frames_left = 0

    def _search_decision(self, mask):
        # 搜索中任何一帧重新看到线就立刻恢复 Reacquire immediately once the line reappears
        corner = _detect_corner_branch(mask, IMAGE_CENTER_X)
        if corner is not None:
            self._engage_lock(corner.mode)
            return corner

        straight = _detect_bottom_line(mask, IMAGE_CENTER_X)
        if straight is not None:
            self._end_search()
            return straight

        self._search_frames_left -= 1
        if self._search_frames_left <= 0:
            # 两个方向都扫完仍没找到：放弃，停车等待，避免无限打转
            # Both sweeps failed: give up and stop instead of spinning forever
            self._end_search()
            self._search_exhausted = True
            return LineDecision(False)

        # 前1/3帧朝最后看到线的一侧扫，剩余帧反向扫
        # Sweep toward the last-seen side first, then the opposite side
        if self._search_frames_left > SEARCH_SWEEP_FRAMES * 2:
            direction = self._search_side
        else:
            direction = -self._search_side

        if direction > 0:
            return LineDecision(
                True,
                x=IMAGE_CENTER_X,
                y=0,
                mode="search_right",
                speed=0,
                turn_limit=SEARCH_TURN,
                turn_override=-SEARCH_TURN,
            )
        return LineDecision(
            True,
            x=IMAGE_CENTER_X,
            y=0,
            mode="search_left",
            speed=0,
            turn_limit=SEARCH_TURN,
            turn_override=SEARCH_TURN,
        )


def choose_line_target(binary, last_x=IMAGE_CENTER_X):
    mask = np.asarray(binary) > 0
    if mask.ndim != 2 or mask.size == 0:
        return LineDecision(False)

    corner = _detect_corner_branch(mask, last_x)
    if corner is not None:
        return corner

    straight = _detect_bottom_line(mask, last_x)
    if straight is not None:
        return straight

    return LineDecision(False)


def _detect_corner_branch(mask, last_x):
    height, _width = mask.shape
    y0, y1 = CORNER_Y_RANGE
    y0 = max(0, min(y0, height - 1))
    y1 = max(y0, min(y1, height - 1))

    best = None
    for y in range(y0, y1 + 1):
        for start, end in _segments(mask[y]):
            width = end - start + 1
            if width < CORNER_MIN_RUN_WIDTH:
                continue
            center = (start + end) // 2
            offset = center - last_x
            if abs(offset) < CORNER_MIN_OFFSET:
                continue
            score = width + abs(offset)
            if best is None or score > best[0]:
                best = (score, y, start, end, center)

    if best is None:
        return None

    _score, y, start, end, center = best
    if center > last_x:
        return LineDecision(
            True,
            x=int(end),
            y=int(y),
            mode="corner_right",
            speed=CORNER_SPEED,
            turn_multiplier=CORNER_TURN_MULTIPLIER,
            turn_limit=CORNER_TURN_LIMIT,
        )

    return LineDecision(
        True,
        x=int(start),
        y=int(y),
        mode="corner_left",
        speed=CORNER_SPEED,
        turn_multiplier=CORNER_TURN_MULTIPLIER,
        turn_limit=CORNER_TURN_LIMIT,
    )


def _detect_bottom_line(mask, last_x, max_jump=None):
    height, width_total = mask.shape
    band_candidates = []
    for band_index, (y0, y1) in enumerate(BOTTOM_BANDS):
        y0 = max(0, min(y0, height - 1))
        y1 = max(y0, min(y1, height - 1))
        candidates = _band_line_candidates(mask, y0, y1, width_total, last_x)
        if candidates:
            _distance, width, center, y = min(candidates, key=lambda item: (item[0], -item[1]))
            band_candidates.append((band_index, width, center, y))

    if not band_candidates:
        return None

    x_values = [center for _band_index, _width, center, _y in band_candidates]
    x_spread = max(x_values) - min(x_values)
    mode = "curve" if len(band_candidates) >= 2 and x_spread >= CURVE_MIN_SPREAD else "straight"
    center, y = _weighted_bottom_target(band_candidates)

    if max_jump is not None:
        allowed_jump = MAX_CURVE_JUMP if mode == "curve" else max_jump
        if abs(center - last_x) > allowed_jump:
            return None

    if mode == "curve":
        return LineDecision(
            True,
            x=int(center),
            y=int(y),
            mode="curve",
            speed=CURVE_SPEED,
            turn_multiplier=CURVE_TURN_MULTIPLIER,
            turn_limit=CURVE_TURN_LIMIT,
        )

    return LineDecision(
        True,
        x=int(center),
        y=int(y),
        mode="straight",
        speed=STRAIGHT_SPEED,
    )


def _band_line_candidates(mask, y0, y1, width_total, last_x):
    band = mask[y0 : y1 + 1, :]
    min_votes = max(2, int(band.shape[0] * 0.25))
    columns = band.sum(axis=0) >= min_votes
    candidates = []
    for start, end in _segments(columns):
        width = end - start + 1
        if width < MIN_SEGMENT_WIDTH:
            continue
        if width > MAX_LINE_WIDTH:
            continue  # 过宽的大色块按阴影处理 Oversized blobs are treated as shadows
        center = (start + end) // 2
        if center < EDGE_REJECT_ZONE or center > width_total - 1 - EDGE_REJECT_ZONE:
            continue  # 贴边色块按阴影处理 Edge-hugging blobs are treated as shadows
        candidates.append((abs(center - last_x), width, center, (y0 + y1) // 2))
    return candidates


def _weighted_bottom_target(band_candidates):
    weighted_x = 0
    weighted_y = 0
    total_weight = 0
    for band_index, _width, center, y in band_candidates:
        weight = BOTTOM_BAND_WEIGHTS[band_index]
        weighted_x += center * weight
        weighted_y += y * weight
        total_weight += weight
    return int(round(weighted_x / total_weight)), int(round(weighted_y / total_weight))


def _segments(row_mask):
    indices = np.flatnonzero(row_mask)
    if len(indices) == 0:
        return []

    segments = []
    start = int(indices[0])
    previous = int(indices[0])
    for value in indices[1:]:
        value = int(value)
        if value == previous + 1:
            previous = value
            continue
        segments.append((start, previous))
        start = previous = value
    segments.append((start, previous))
    return segments
