"""Agent configuration loader — makes every agent's model swappable.

Reads config/agents.yaml at the project root. Missing file or keys fall
back to sensible defaults, so the system runs even with no config.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from mes.db.session import PROJECT_ROOT

CONFIG_PATH = PROJECT_ROOT / "config" / "agents.yaml"

DEFAULTS: dict[str, Any] = {
    "default_model": "sonnet",
    "orchestrator": {"model": "inherit", "max_turns": 40},
    "agents": {},
}


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        loaded = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        cfg.update({k: v for k, v in loaded.items() if v is not None})
    cfg.setdefault("agents", {})
    return cfg


def agent_setting(name: str) -> dict[str, Any]:
    cfg = load_config()
    entry = cfg["agents"].get(name) or {}
    return {
        "model": entry.get("model") or cfg["default_model"],
        "enabled": entry.get("enabled", True),
    }


def orchestrator_setting() -> dict[str, Any]:
    cfg = load_config()
    entry = cfg.get("orchestrator") or {}
    return {
        "model": entry.get("model", "inherit"),
        "max_turns": int(entry.get("max_turns", 40)),
    }
