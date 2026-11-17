"""Load settings such as ANTHROPIC_API_KEY from a local .env file (KEY=value per line).

Variables already set in the environment win over the file.
"""
from __future__ import annotations

import os
from pathlib import Path
from .paths import data_root

ENV_FILE = data_root() / ".env"


def load_env(path: Path = ENV_FILE) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and value and not os.environ.get(key):
            os.environ[key] = value


def claude_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
