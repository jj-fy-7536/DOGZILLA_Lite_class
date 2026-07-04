"""QR station decisions for line following."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class StationDecision:
    reached: bool
    station: str
    log_line: str


def station_decision(codes: list[str] | tuple[str, ...], target_station: str) -> StationDecision | None:
    if not target_station or not codes:
        return None
    for code in codes:
        station = str(code).strip()
        if not station:
            continue
        if station == target_station:
            return StationDecision(True, station, "STATION_REACHED {}".format(station))
    station = str(codes[0]).strip()
    if not station:
        return None
    return StationDecision(False, station, "STATION_SEEN {}".format(station))


def write_line_result(
    result_path: Path,
    *,
    success: bool,
    target_station: str,
    reached_station: str,
    mode: str,
    timestamp: str | None = None,
) -> None:
    result_path = Path(result_path)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "success": bool(success),
        "target_station": target_station,
        "reached_station": reached_station,
        "mode": mode,
        "timestamp": timestamp or datetime.now().strftime("%Y%m%d_%H%M%S"),
    }
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
