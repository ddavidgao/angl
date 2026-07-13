from __future__ import annotations

import json
import os
from pathlib import Path


def config_path() -> Path:
    root = os.environ.get("ANGL_CONFIG_DIR")
    if root:
        return Path(root) / "config.json"
    return Path.home() / ".angl" / "config.json"


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def get_config_value(key: str, env_names=(), default=None):
    for name in env_names:
        value = os.environ.get(name)
        if value:
            return value
    return load_config().get(key, default)
