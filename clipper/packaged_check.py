"""Installer smoke checks; no existing jobs, sign-ins or model downloads."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import traceback


def run(report: Path) -> int:
    result = {"ok": False}
    try:
        from .paths import configure_runtime, data_root, resource_root
        if not os.environ.get("SNAP_NINJA_DATA_DIR"):
            raise RuntimeError("Self-test requires an explicitly isolated SNAP_NINJA_DATA_DIR")
        configure_runtime()
        root = data_root()
        # Verify native Python libraries, pywebview/.NET, resources and API imports.
        import cv2
        import ctranslate2
        import faster_whisper
        import onnxruntime
        import yt_dlp
        import yt_dlp_ejs
        import webview.platforms.winforms
        from unittest.mock import patch
        with patch("clipper.jobs.JobStore"):
            import app
        with app.app.test_client() as client:
            response = client.get("/")
            if response.status_code != 200 or b"Snap Ninja" not in response.data:
                raise RuntimeError("Packaged UI could not be served")
        for name in ("ffmpeg", "ffprobe", "node"):
            if not shutil.which(name):
                raise RuntimeError(f"Packaged tool missing: {name}")
            subprocess.run([name, "--version" if name == "node" else "-version"],
                           check=True, capture_output=True, timeout=30)
        if not (resource_root() / "clipper/models/face_detection_yunet_2023mar.onnx").is_file():
            raise RuntimeError("Packaged face detector missing")
        subtitle = root / "test.srt"
        subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\nSnap Ninja\n", encoding="utf-8")
        # Test the actual encoding and libass path used by captions, with generated media.
        output = root / "smoke.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1",
                        "-vf", "subtitles=test.srt", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output)],
                       cwd=root, check=True, capture_output=True, timeout=60)
        from .media import probe
        if probe(output).duration <= 0:
            raise RuntimeError("Packaged video render produced no playable output")
        result.update(ok=True, data_root=str(root), resources=str(resource_root()))
    except Exception:
        result["error"] = traceback.format_exc()
    report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1
