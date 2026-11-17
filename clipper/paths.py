"""Installed resources are read-only; user data survives updates and uninstalling.

Source checkouts retain their existing locations. Nothing migrates automatically.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def data_root() -> Path:
    override = os.environ.get("SNAP_NINJA_DATA_DIR")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError("SNAP_NINJA_DATA_DIR must be an absolute folder path.")
        return path
    if not frozen():
        return resource_root()
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("Windows LOCALAPPDATA is unavailable.")
    return Path(local) / "SnapNinja"


def configure_runtime() -> None:
    """Only the application's children see its private media/download tools."""
    if not frozen():
        return
    base = resource_root()
    install = Path(sys.executable).resolve().parent
    bins = [install / "dependencies" / "ffmpeg", base / "vendor" / "node"]
    os.environ["PATH"] = os.pathsep.join(str(p) for p in bins) + os.pathsep + os.environ.get("PATH", "")
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(root / "models" / "huggingface"))


def picker_command(result: Path) -> list[str]:
    if frozen():
        return [sys.executable, "--pick-output", str(result)]
    return [sys.executable, str(resource_root() / "tools" / "pick_output.py"), str(result)]
