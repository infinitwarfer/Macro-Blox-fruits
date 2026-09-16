from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "InfinityxWare"
APP_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = APP_DIR / "config.json"


DEFAULT_CONFIG: dict[str, Any] = {
    "macros": {
        "r_x": {"enabled": False},
        "f_x": {"enabled": False},
        "spam_r": {"enabled": False, "interval_ms": 10},
    },
    "pixel": {
        "x": 759,
        "y": 624,
        "target_color": 0xFFFFFF,
        "mode": "X",
        "action": "click",
        "cooldown_ms": 15000,
    },
    "boost": {
        "key": "z",
        "interval_ms": 100,
        "enabled": False,
    },
    "custom_macros": {},
}


def load_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))

    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return json.loads(json.dumps(DEFAULT_CONFIG))

    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    _deep_update(cfg, data)
    return cfg


def save_config(data: dict[str, Any]) -> None:
    CONFIG_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _deep_update(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_update(dst[key], value)
        else:
            dst[key] = value
