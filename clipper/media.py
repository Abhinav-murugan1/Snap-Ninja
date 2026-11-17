"""ffmpeg / ffprobe helpers."""
from __future__ import annotations

import functools
import json
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

# Hide console windows spawned from a GUI/background process on Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class MediaError(RuntimeError):
    pass


def run(cmd: list[str], cwd: str | Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [str(c) for c in cmd],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )
    if check and proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise MediaError(f"Command failed ({proc.returncode}): {' '.join(map(str, cmd[:6]))} ...\n{tail}")
    return proc


@dataclass
class VideoInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    rotation: int = 0
    audio_tracks: int = 0  # ShadowPlay/OBS often store game sound and mic as separate tracks

    @property
    def display_size(self) -> tuple[int, int]:
        if self.rotation in (90, 270, -90, -270):
            return self.height, self.width
        return self.width, self.height


def probe(path: str | Path) -> VideoInfo:
    proc = run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if v is None:
        raise MediaError("No video stream found in file.")
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    duration = float(data.get("format", {}).get("duration") or v.get("duration") or 0)
    fps = 30.0
    rate = v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1"
    try:
        num, den = rate.split("/")
        fps = float(num) / float(den) if float(den) else 30.0
    except (ValueError, ZeroDivisionError):
        pass
    rotation = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(float(sd["rotation"]))
    if not rotation and v.get("tags", {}).get("rotate"):
        rotation = int(v["tags"]["rotate"])
    return VideoInfo(
        path=str(path),
        duration=duration,
        width=int(v.get("width", 0)),
        height=int(v.get("height", 0)),
        fps=fps or 30.0,
        has_audio=bool(audio),
        rotation=rotation,
        audio_tracks=len(audio),
    )


def audio_mix(input_index: int, tracks: int, label: str) -> str:
    """Filter that merges every audio track of an input into one stream (silent tracks add nothing)."""
    if tracks <= 1:
        return f"[{input_index}:a:0]anull[{label}]"
    ins = "".join(f"[{input_index}:a:{i}]" for i in range(tracks))
    return f"{ins}amix=inputs={tracks}:duration=longest:dropout_transition=0:normalize=0[{label}]"


def extract_audio(src: str | Path, dst: str | Path, tracks: int = 1, sample_rate: int = 16000) -> Path:
    dst = Path(dst)
    if dst.exists() and dst.stat().st_size > 1000:
        return dst
    run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn",
         "-filter_complex", audio_mix(0, tracks, "a"), "-map", "[a]", "-ac", "1",
         "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst)])
    return dst


def audio_rms(wav_path: str | Path, hop: float = 0.5) -> tuple[np.ndarray, float]:
    """Loudness (dBFS) per `hop` seconds window. Streams the file so long videos stay cheap."""
    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        width = wf.getsampwidth()
        if width != 2:
            raise MediaError("Expected 16-bit PCM wav")
        hop_frames = max(1, int(sr * hop))
        values = []
        while True:
            raw = wf.readframes(hop_frames * 200)
            if not raw:
                break
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            n = len(x) // hop_frames
            if n:
                blocks = x[: n * hop_frames].reshape(n, hop_frames)
                values.append(np.sqrt(np.mean(blocks ** 2, axis=1) + 1e-12))
            rest = x[n * hop_frames:]
            if len(rest):
                values.append(np.array([np.sqrt(np.mean(rest ** 2) + 1e-12)], dtype=np.float32))
    rms = np.concatenate(values) if values else np.zeros(1, dtype=np.float32)
    return 20 * np.log10(rms + 1e-9), hop


_TIME_RE = re.compile(r"pts_time:([0-9.]+)")
_SCORE_RE = re.compile(r"lavfi\.scene_score=([0-9.]+)")


def scene_activity(src: str | Path, duration: float, hop: float = 0.5) -> np.ndarray:
    """Visual change score per hop. Decodes keyframes only at low resolution, so it is fast."""
    bins = np.zeros(max(1, int(np.ceil(duration / hop)) + 1), dtype=np.float32)
    proc = run([
        FFMPEG, "-hide_banner", "-skip_frame", "nokey", "-i", str(src), "-an",
        "-vf", "scale=160:-2,select='gte(scene,0)',metadata=print:file=-",
        "-fps_mode", "vfr", "-f", "null", "-",
    ], check=False)
    if proc.returncode != 0:
        raise MediaError("Visual activity scan failed: " + proc.stderr.strip()[-300:])
    current_t = None
    for line in proc.stdout.splitlines():
        m = _TIME_RE.search(line)
        if m:
            current_t = float(m.group(1))
            continue
        m = _SCORE_RE.search(line)
        if m and current_t is not None:
            i = int(current_t / hop)
            if 0 <= i < len(bins):
                bins[i] = max(bins[i], float(m.group(1)))
    return bins


@functools.lru_cache(maxsize=1)
def video_encoder() -> list[str]:
    """Prefer NVENC when it actually works on this machine, else libx264."""
    try:
        proc = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=256x256:d=0.5", "-c:v", "h264_nvenc", "-f", "null", "-"], check=False)
        if proc.returncode == 0:
            # cq 19 + adaptive quantization keeps caption edges and fine detail clean
            return ["-c:v", "h264_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", "19",
                    "-b:v", "0", "-spatial-aq", "1", "-temporal-aq", "1", "-aq-strength", "8",
                    "-profile:v", "high", "-pix_fmt", "yuv420p"]
    except OSError:
        pass
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-tune", "film", "-pix_fmt", "yuv420p"]


def thumbnail(src: str | Path, t: float, dst: str | Path, width: int = 480) -> None:
    run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(src),
         "-frames:v", "1", "-vf", f"scale={width}:-2", str(dst)], check=False)


def grab_frames(src: str | Path, times: list[float], width: int = 640) -> list[bytes]:
    """JPEG frames at the given times (seconds), small enough for a vision model."""
    frames = []
    for t in times:
        proc = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(src),
             "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            capture_output=True, creationflags=_NO_WINDOW)
        if proc.returncode == 0 and proc.stdout:
            frames.append(proc.stdout)
    return frames
