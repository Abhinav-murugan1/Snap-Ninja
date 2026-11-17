"""Speech-to-text with word timestamps (faster-whisper)."""
from __future__ import annotations

import json
import os
import site
import sys
import wave
from pathlib import Path
from typing import Callable

import numpy as np

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

ProgressFn = Callable[[float, str], None]


def _register_cuda_dlls() -> None:
    """On Windows, pip-installed nvidia-cublas/cudnn wheels put DLLs in site-packages/nvidia/*/bin."""
    if sys.platform != "win32":
        return
    roots = list(site.getsitepackages()) + [site.getusersitepackages()]
    for root in roots:
        nv = Path(root) / "nvidia"
        if not nv.is_dir():
            continue
        for bin_dir in nv.glob("*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))
            except (OSError, AttributeError):
                pass
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


CHUNK_SECONDS = 600


def _audio_chunks(wav_path: str | Path, chunk_s: float = CHUNK_SECONDS, search_s: float = 8.0):
    """Yield (offset_seconds, float32 mono 16 kHz array) pieces of ~chunk_s, cut at the quietest
    point near each boundary so words are not split. Avoids loading hours of audio at once and
    avoids faster-whisper's PyAV decoder (incompatible with recent PyAV releases)."""
    with wave.open(str(wav_path), "rb") as wf:
        sr = wf.getframerate()
        if sr != 16000 or wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError("Expected 16 kHz mono 16-bit wav")
        total = wf.getnframes()
        pos = 0
        carry = np.zeros(0, dtype=np.int16)
        while pos < total or len(carry):
            want = int(chunk_s * sr) + int(search_s * sr) - len(carry)
            raw = wf.readframes(max(0, want)) if pos < total else b""
            data = np.concatenate([carry, np.frombuffer(raw, dtype=np.int16)])
            read_to = pos + len(data) - len(carry)
            if read_to >= total or len(data) <= int(chunk_s * sr):
                cut = len(data)
            else:
                lo, hi = int((chunk_s - search_s) * sr), len(data)
                win = int(0.1 * sr)
                region = data[lo:hi].astype(np.float32)
                n = len(region) // win
                energy = (region[: n * win].reshape(n, win) ** 2).mean(axis=1)
                cut = lo + int(np.argmin(energy)) * win + win // 2
            offset = (read_to - len(data)) / sr
            yield offset, data[:cut].astype(np.float32) / 32768.0
            carry = data[cut:]
            pos = read_to
            if pos >= total and not len(carry):
                break


def _norm(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum() or ch == " ").strip()


def _looks_hallucinated(seg: dict, previous: list[dict]) -> bool:
    """Whisper invents text over music, game sound and silence. Typical signs: a phrase repeated
    back-to-back, a few words stretched over many seconds, or low confidence with no speech."""
    words = seg["words"]
    if not words or not _norm(seg["text"]):
        return True
    # segment bounds often include long silence; the word timestamps are more reliable
    seg["start"], seg["end"] = words[0]["start"], words[-1]["end"]
    if seg.get("no_speech", 0) > 0.6 and seg.get("logprob", 0) < -0.8:
        return True
    mean_prob = sum(w["prob"] for w in words) / len(words)
    if mean_prob < 0.15:
        return True
    dur = seg["end"] - seg["start"]
    if dur / len(words) > 1.5:  # e.g. "I can't do that." spread over 30 s
        return True
    text = _norm(seg["text"])
    recent = [_norm(p["text"]) for p in previous[-3:]]
    # unsure repeat of a sentence just said; short shouts ("let's go!") really do get repeated
    if mean_prob < 0.5 and len(text.split()) >= 4 and text in recent:
        return True
    return False


def clean_segments(segments: list[dict]) -> list[dict]:
    kept: list[dict] = []
    for seg in segments:
        if not _looks_hallucinated(dict(seg), kept):
            s = dict(seg)
            s["start"], s["end"] = s["words"][0]["start"], s["words"][-1]["end"]
            kept.append(s)
    return kept


def _cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _pick_model(name: str, cuda: bool) -> str:
    if name and name != "auto":
        return name
    return "large-v3-turbo" if cuda else "small"


def transcribe(audio_path: str | Path, cache_path: str | Path, duration: float, model_name: str = "auto",
               language: str = "", progress: ProgressFn | None = None) -> dict:
    """Return {"language": str, "segments": [{start, end, text, words: [{start, end, word, prob}]}]}."""
    cache_path = Path(cache_path)
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        cached["segments"] = clean_segments(cached["segments"])
        return cached

    _register_cuda_dlls()
    from faster_whisper import WhisperModel

    attempts = []
    if _cuda_available():
        attempts.append(("cuda", "float16"))
    attempts.append(("cpu", "int8"))

    last_err: Exception | None = None
    for device, compute in attempts:
        name = _pick_model(model_name, device == "cuda")
        try:
            if progress:
                progress(0.0, f"Loading Whisper '{name}' on {device}")
            model = WhisperModel(name, device=device, compute_type=compute)
            segments = []
            lang = language or None
            for offset, audio in _audio_chunks(audio_path):
                segments_iter, info = model.transcribe(
                    audio,
                    language=lang,
                    word_timestamps=True,
                    vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 500},
                    beam_size=5,
                    condition_on_previous_text=False,
                    hallucination_silence_threshold=2.0,
                )
                lang = lang or info.language  # keep the first detected language for later chunks
                for seg in segments_iter:
                    words = [
                        {"start": round(offset + w.start, 3), "end": round(offset + w.end, 3),
                         "word": w.word.strip(), "prob": round(w.probability, 3)}
                        for w in (seg.words or []) if w.word.strip()
                    ]
                    end = offset + seg.end
                    candidate = {"start": round(offset + seg.start, 3), "end": round(end, 3),
                                 "text": seg.text.strip(), "words": words,
                                 "logprob": round(seg.avg_logprob, 3), "no_speech": round(seg.no_speech_prob, 3)}
                    if not _looks_hallucinated(candidate, segments):
                        segments.append(candidate)
                    if progress and duration > 0:
                        progress(min(end / duration, 1.0),
                                 f"Transcribing on {device.upper()}: {end / 60:.1f} of {duration / 60:.1f} min")
            result = {"language": lang or "", "model": name, "device": device, "segments": segments}
            cache_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            return result
        except Exception as exc:  # CUDA libs missing, OOM, etc. -> retry on CPU
            last_err = exc
            if progress:
                progress(0.0, f"Whisper on {device} failed ({type(exc).__name__}: {exc}); retrying")
    raise RuntimeError(f"Transcription failed: {last_err}")
