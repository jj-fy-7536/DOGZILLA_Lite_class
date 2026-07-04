"""Configuration and alias resolution for the minimal housekeeper workflow."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Defaults:
    color: str = "red"
    target_station: str = "station_A"


@dataclass(frozen=True)
class ReturnHomeConfig:
    enabled: bool = True
    turn_speed: int = 20
    turn_seconds: float = 2.4
    timeout_seconds: float = 90.0


@dataclass(frozen=True)
class LineConfig:
    qr_decode_every_frames: int = 3
    result_path: str = "/home/pi/xgoPictures/housekeeper/line_result.json"


@dataclass(frozen=True)
class AliasEntry:
    label: str
    aliases: tuple[str, ...]


def clean_alias_text(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?;；:：\"'“”‘’_-]", "", text.strip().lower())


DEFAULT_DATA: dict[str, Any] = {
    "defaults": {
        "color": "red",
        "target_station": "station_A",
    },
    "colors": {
        "red": {"label": "红球", "aliases": ["红", "红色", "红球", "红色方块"]},
        "green": {"label": "绿球", "aliases": ["绿", "绿色", "绿球", "绿色方块"]},
        "blue": {"label": "蓝球", "aliases": ["蓝", "蓝色", "蓝球", "蓝色方块"]},
        "yellow": {"label": "黄球", "aliases": ["黄", "黄色", "黄球", "黄色方块"]},
    },
    "stations": {
        "home": {"label": "待命区", "aliases": ["家", "回家", "起点", "待命区"]},
        "station_A": {"label": "客厅", "aliases": ["客厅", "A点", "站点A", "stationA", "station_A"]},
        "station_B": {"label": "门口", "aliases": ["门口", "门边", "B点", "站点B", "stationB", "station_B"]},
    },
    "return_home": {
        "enabled": True,
        "turn_speed": 20,
        "turn_seconds": 2.4,
        "timeout_seconds": 90.0,
    },
    "line": {
        "qr_decode_every_frames": 3,
        "result_path": "/home/pi/xgoPictures/housekeeper/line_result.json",
    },
}


@dataclass(frozen=True)
class HousekeeperConfig:
    defaults: Defaults
    colors: dict[str, AliasEntry]
    stations: dict[str, AliasEntry]
    return_home: ReturnHomeConfig
    line: LineConfig

    @classmethod
    def default(cls) -> "HousekeeperConfig":
        return cls.from_dict(DEFAULT_DATA)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HousekeeperConfig":
        merged = _deep_merge(DEFAULT_DATA, data)
        defaults = merged.get("defaults", {})
        return_home = merged.get("return_home", {})
        line = merged.get("line", {})
        return cls(
            defaults=Defaults(
                color=str(defaults.get("color", "red")),
                target_station=str(defaults.get("target_station", "station_A")),
            ),
            colors=_parse_alias_entries(merged.get("colors", {}), "colors"),
            stations=_parse_alias_entries(merged.get("stations", {}), "stations"),
            return_home=ReturnHomeConfig(
                enabled=bool(return_home.get("enabled", True)),
                turn_speed=int(return_home.get("turn_speed", 20)),
                turn_seconds=float(return_home.get("turn_seconds", 2.4)),
                timeout_seconds=float(return_home.get("timeout_seconds", 90.0)),
            ),
            line=LineConfig(
                qr_decode_every_frames=max(1, int(line.get("qr_decode_every_frames", 3))),
                result_path=str(line.get("result_path", "/home/pi/xgoPictures/housekeeper/line_result.json")),
            ),
        )

    def resolve_color(self, text: str) -> str | None:
        return self._resolve_alias(text, self.colors)

    def resolve_station(self, text: str) -> str | None:
        return self._resolve_alias(text, self.stations)

    def color_label(self, color: str) -> str:
        entry = self.colors.get(color)
        return entry.label if entry is not None else color

    def station_label(self, station: str) -> str:
        entry = self.stations.get(station)
        return entry.label if entry is not None else station

    @staticmethod
    def _resolve_alias(text: str, entries: dict[str, AliasEntry]) -> str | None:
        cleaned = clean_alias_text(text)
        best_key = None
        best_len = -1
        for key, entry in entries.items():
            aliases = (key, entry.label, *entry.aliases)
            for alias in aliases:
                cleaned_alias = clean_alias_text(alias)
                if cleaned_alias and cleaned_alias in cleaned and len(cleaned_alias) > best_len:
                    best_key = key
                    best_len = len(cleaned_alias)
        return best_key


def load_config(path: Path | None) -> HousekeeperConfig:
    if path is None or not Path(path).exists():
        return HousekeeperConfig.default()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError("配置文件不是有效 JSON: {}".format(path)) from exc
    except OSError as exc:
        raise ConfigError("配置文件读取失败: {}".format(path)) from exc
    if not isinstance(data, dict):
        raise ConfigError("配置文件顶层必须是 JSON object: {}".format(path))
    return HousekeeperConfig.from_dict(data)


def _parse_alias_entries(raw: object, section: str) -> dict[str, AliasEntry]:
    if not isinstance(raw, dict):
        raise ConfigError("{} 必须是 object".format(section))
    result: dict[str, AliasEntry] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            raise ConfigError("{}:{} 必须是 object".format(section, key))
        aliases = value.get("aliases", [])
        if not isinstance(aliases, list):
            raise ConfigError("{}:{} aliases 必须是 list".format(section, key))
        result[str(key)] = AliasEntry(
            label=str(value.get("label", key)),
            aliases=tuple(str(alias) for alias in aliases),
        )
    return result


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in base.items():
        if isinstance(value, dict):
            result[key] = _deep_merge(value, {})
        else:
            result[key] = value
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
