"""Detect large black stop blocks while following a line."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


DEFAULT_MIN_AREA_RATIO = 0.35
DEFAULT_MIN_WIDTH_RATIO = 0.55
DEFAULT_REQUIRED_FRAMES = 5


def is_stop_marker(
    binary,
    *,
    min_area_ratio: float = DEFAULT_MIN_AREA_RATIO,
    min_width_ratio: float = DEFAULT_MIN_WIDTH_RATIO,
) -> bool:
    mask = np.asarray(binary) > 0
    if mask.ndim != 2 or mask.size == 0:
        return False
    height, width = mask.shape
    area_ratio = float(mask.mean())
    if area_ratio < min_area_ratio:
        return False
    columns = np.any(mask, axis=0)
    width_ratio = float(columns.sum()) / float(width)
    if width_ratio < min_width_ratio:
        return False
    rows = np.any(mask, axis=1)
    height_ratio = float(rows.sum()) / float(height)
    return height_ratio >= min_width_ratio * 0.5


@dataclass
class StopMarkerDetector:
    required_frames: int = DEFAULT_REQUIRED_FRAMES
    min_area_ratio: float = DEFAULT_MIN_AREA_RATIO
    min_width_ratio: float = DEFAULT_MIN_WIDTH_RATIO
    count: int = 0

    def update(self, binary) -> bool:
        if is_stop_marker(
            binary,
            min_area_ratio=self.min_area_ratio,
            min_width_ratio=self.min_width_ratio,
        ):
            self.count += 1
        else:
            self.count = 0
        return self.count >= max(1, int(self.required_frames))
