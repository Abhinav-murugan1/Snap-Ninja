"""Your 👍/👎 votes on clips, remembered across jobs so the local AI learns what you like."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from .paths import data_root

ROOT = data_root() / "feedback"
FILE = ROOT / "votes.json"
_lock = threading.Lock()


def load() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def set_vote(job: dict, index: int, vote: str | None, clip_text: str) -> None:
    """vote: "up", "down" or None (clear)."""
    key = f"{job['id']}:{index}"
    clip = job["clips"][index]
    sit = job.get("situation") or {}
    with _lock:
        store = load()
        if vote is None:
            store.pop(key, None)
        else:
            store[key] = {
                "vote": vote, "t": time.time(), "video_type": sit.get("video_type", ""),
                "topic": sit.get("game_or_topic", ""), "kind": clip.get("category", ""),
                "title": clip.get("title", ""), "why": clip.get("reason", ""), "text": clip_text[:400],
                "duration": round(clip.get("duration", 0), 1),
            }
        ROOT.mkdir(exist_ok=True)
        FILE.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")


def counts() -> dict:
    store = load()
    return {"up": sum(v["vote"] == "up" for v in store.values()),
            "down": sum(v["vote"] == "down" for v in store.values())}
