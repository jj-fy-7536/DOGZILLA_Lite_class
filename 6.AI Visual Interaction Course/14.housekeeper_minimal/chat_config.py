"""Shared AI chat provider configuration for the housekeeper modules."""

from __future__ import annotations

import os
from typing import Mapping, NamedTuple


DEFAULT_DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_SPARK_API_URL = "https://spark-api-open.xf-yun.com/v1/chat/completions"
DEFAULT_SPARK_MODEL = "lite"


class ChatDefaults(NamedTuple):
    provider: str
    api_key: str
    api_url: str
    model: str


def _env(env: Mapping[str, str], name: str, default: str = "") -> str:
    return env.get(name, default).strip() or default


def resolve_chat_defaults(env: Mapping[str, str] | None = None) -> ChatDefaults:
    env = os.environ if env is None else env
    deepseek_key = _env(env, "DEEPSEEK_API_KEY")
    if deepseek_key:
        return ChatDefaults(
            provider="deepseek",
            api_key=deepseek_key,
            api_url=_env(env, "DEEPSEEK_API_URL", DEFAULT_DEEPSEEK_API_URL),
            model=_env(env, "DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
        )

    return ChatDefaults(
        provider="spark",
        api_key=_env(env, "SPARK_API_PASSWORD"),
        api_url=_env(env, "SPARK_API_URL", DEFAULT_SPARK_API_URL),
        model=_env(env, "SPARK_MODEL", DEFAULT_SPARK_MODEL),
    )
