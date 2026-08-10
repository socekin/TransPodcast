from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files


@lru_cache
def zh_cn(key: str) -> str:
    payload = json.loads(
        files("transpodcast.i18n")
        .joinpath("zh-CN.json")
        .read_text(encoding="utf-8")
    )
    value = payload.get(key)
    if not isinstance(value, str):
        raise KeyError(f"Unknown zh-CN localization key: {key}")
    return value
