"""Hype detector: find loud, intense moments in the audio.

Per 0.5 s step it measures:
  jump     loudness compared with the previous 30 s (a sudden spike, even in an always-loud stream)
  loud     loudness compared with the whole video
  shout    share of energy in the 1-4 kHz band, where shouting and screaming put their power
  crowd    noise-like sound while loud: cheering, applause, laughter, a crowd going off
  impacts  sharp sound onsets while loud: gunfire, explosions, hits, slams
  words    hype words, exclamations and repeated shouts in the transcript

These are combined into one hype curve. Its peaks become hype events: when the moment peaked, how long
it built and faded, how strong it was and what kind of hype it was.
"""
from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SR = 16000
NFFT = 8192
KINDS = {
    "shout": "shouting or screaming",
    "crowd": "crowd reaction (laughter, cheering, applause)",
    "impacts": "action burst (shots, hits, explosions)",
    "words": "hype words",
    "jump": "sudden loud spike",
    "loud": "loud stretch",
}
WEIGHTS = {"jump": 0.26, "loud": 0.20, "shout": 0.18, "crowd": 0.12, "impacts": 0.10, "words": 0.14}


@dataclass
class HypeEvent:
    peak: float       # seconds
    start: float      # where the build-up begins
    end: float        # where it has died down
    strength: float
    kind: str         # key of KINDS

    @property
    def label(self) -> str:
        return KINDS.get(self.kind, "hype")


# --------------------------------------------------------------------------- audio features

def audio_features(wav_path: str | Path, hop: float = 0.5) -> dict[str, np.ndarray]:
    """Spectral hype features per hop from a 16 kHz mono 16-bit wav. Streams the file in blocks."""
    hop_n = int(SR * hop)
    sub_n = hop_n // 10                       # 50 ms sub-frames for onset counting
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    band_all = (freqs >= 80) & (freqs <= 7000)
    band_shout = (freqs >= 1000) & (freqs <= 4000)
    band_flat = (freqs >= 300) & (freqs <= 6000)
    window = np.hanning(hop_n).astype(np.float32)
    out = {"shout": [], "crowd": [], "impacts": []}
    prev_sub_db = None
    with wave.open(str(wav_path), "rb") as wf:
        if wf.getframerate() != SR or wf.getsampwidth() != 2 or wf.getnchannels() != 1:
            raise ValueError("Expected 16 kHz mono 16-bit wav")
        while True:
            raw = wf.readframes(hop_n * 240)  # 2 minutes per block
            if not raw:
                break
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            n = int(np.ceil(len(x) / hop_n))
            x = np.pad(x, (0, n * hop_n - len(x)))
            frames = x.reshape(n, hop_n)

            spec = np.abs(np.fft.rfft(frames * window, n=NFFT, axis=1)) ** 2 + 1e-12
            total = spec[:, band_all].sum(axis=1)
            out["shout"].append(spec[:, band_shout].sum(axis=1) / total)
            flat_band = spec[:, band_flat]
            flatness = np.exp(np.mean(np.log(flat_band), axis=1)) / np.mean(flat_band, axis=1)
            out["crowd"].append(flatness)

            sub_db = 10 * np.log10(np.mean(frames.reshape(n, 10, sub_n) ** 2, axis=2) + 1e-10).ravel()
            seq = sub_db if prev_sub_db is None else np.concatenate([[prev_sub_db], sub_db])
            rise = np.diff(seq)
            if prev_sub_db is None:
                rise = np.concatenate([[0.0], rise])
            onsets = ((rise > 8.0) & (sub_db > -45.0)).reshape(n, 10).sum(axis=1).astype(np.float32)
            out["impacts"].append(onsets)
            prev_sub_db = sub_db[-1]
    return {k: (np.concatenate(v) if v else np.zeros(0)) for k, v in out.items()}


# --------------------------------------------------------------------------- hype curve

def _z(x: np.ndarray) -> np.ndarray:
    """Robust z-score that stays sane for mostly-zero signals."""
    if not len(x):
        return x
    med = np.median(x)
    scale = max(np.median(np.abs(x - med)) * 1.4826, 0.5 * float(np.std(x)), 1e-6)
    return np.clip((x - med) / scale, -3, 6)


def _smooth(x: np.ndarray, k: int) -> np.ndarray:
    if k <= 1 or len(x) < k:
        return x
    return np.convolve(x, np.ones(k) / k, mode="same")


def _rolling_prev_mean(x: np.ndarray, k: int) -> np.ndarray:
    """Mean of the k values before each position (the recent baseline)."""
    cs = np.concatenate([[0.0], np.cumsum(x)])
    idx = np.arange(len(x))
    lo = np.maximum(0, idx - k)
    cnt = np.maximum(1, idx - lo)
    base = (cs[idx] - cs[lo]) / cnt
    base[0] = x[0] if len(x) else 0.0
    return base


def _fit(a: np.ndarray, n: int, fill: float = 0.0) -> np.ndarray:
    out = np.full(n, fill, dtype=np.float64)
    m = min(n, len(a))
    out[:m] = a[:m]
    return out


def hype_curve(loud_db: np.ndarray, feats: dict[str, np.ndarray] | None, excite: np.ndarray,
               hop: float, words: np.ndarray | None = None) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Returns (smoothed hype score per hop, components). Score ~0 normally, 1-3 for hype, more for peaks."""
    n = len(excite)
    db = _fit(loud_db, n, float(np.median(loud_db)) if len(loud_db) else -60.0)
    loud = _z(db)
    gate = np.clip(loud / 1.5, 0, 1)          # timbre only counts when it is actually loud
    feats = feats or {}
    comps = {
        "jump": np.clip(_z(db - _rolling_prev_mean(db, int(30 / hop))), 0, None),
        "loud": np.clip(loud, 0, None),
        "shout": np.clip(_z(_fit(feats.get("shout", np.zeros(0)), n)), 0, None) * gate,
        "crowd": np.clip(_z(_fit(feats.get("crowd", np.zeros(0)), n)), 0, None) * gate,
        "impacts": np.clip(_z(_smooth(_fit(feats.get("impacts", np.zeros(0)), n), 4)), 0, None) * gate,
    }
    ex = _smooth(excite, int(3 / hop))
    comps["words"] = np.clip(ex / (np.percentile(ex, 99) + 1e-6), 0, 2) * 2.5 if ex.any() else np.zeros(n)
    # words per second (not scored; tells a shouting speaker apart from a crowd reacting)
    comps["speech"] = _smooth(_fit(words, n), int(2 / hop)) / hop if words is not None else np.ones(n)
    raw = sum(WEIGHTS[k] * comps[k] for k in WEIGHTS)
    # sustained intensity matters: blend a 2 s and a 6 s view
    score = 0.6 * _smooth(raw, int(2 / hop)) + 0.4 * _smooth(raw, int(6 / hop))
    return score, comps


def find_events(score: np.ndarray, comps: dict[str, np.ndarray], hop: float, max_events: int = 60,
                min_gap: float = 20.0) -> list[HypeEvent]:
    if not len(score):
        return []
    thresh = max(0.4, float(np.percentile(score, 93)))  # top ~7% of the video, never plain talk
    order = np.argsort(-score)
    events: list[HypeEvent] = []
    gap = int(min_gap / hop)
    taken = np.zeros(len(score), dtype=bool)
    for i in order:
        if score[i] < thresh or len(events) >= max_events:
            break
        if taken[max(0, i - gap):i + gap + 1].any():
            continue
        peak = float(score[i])
        lo = i
        while lo > 0 and score[lo - 1] > 0.35 * peak and (i - lo) * hop < 20:
            lo -= 1
        hi = i
        while hi + 1 < len(score) and score[hi + 1] > 0.35 * peak and (hi - i) * hop < 15:
            hi += 1
        span = slice(max(0, i - int(2 / hop)), i + int(2 / hop) + 1)
        contrib = {k: WEIGHTS[k] * float(np.mean(comps[k][span])) for k in WEIGHTS}
        kind = max(("shout", "crowd", "impacts", "words", "jump"), key=lambda k: contrib[k])
        if contrib[kind] < 0.15 * peak:
            kind = "loud"
        # loud vocal energy while (almost) nobody is talking in the transcript = the audience/crowd reacting
        if kind in ("shout", "crowd", "loud", "jump") and float(np.mean(comps["speech"][span])) < 0.8:
            kind = "crowd" if contrib["shout"] + contrib["crowd"] > 0.25 * peak else kind
        sustain = (hi - lo) * hop
        events.append(HypeEvent(peak=i * hop, start=lo * hop, end=(hi + 1) * hop,
                                strength=round(peak * (1 + 0.04 * min(sustain, 12)), 3), kind=kind))
        taken[i] = True
    events.sort(key=lambda e: e.peak)
    return events


def event_window(ev: HypeEvent, min_len: float, max_len: float, duration: float,
                 setup: float = 10.0, reaction: float = 3.0) -> tuple[float, float]:
    """Clip range around a hype event: set-up before the build-up, the peak, and the reaction after.
    Extra room goes mostly before the moment (context), since the reaction tail is added later."""
    start = ev.start - setup
    end = ev.end + reaction
    length = end - start
    if length < min_len:
        deficit = min_len - length
        start -= 0.7 * deficit
        end += 0.3 * deficit
    elif length > max_len:
        excess = length - max_len
        cut_setup = min(excess, max(0.0, setup - 3))
        start += cut_setup
        excess -= cut_setup
        end -= excess
    if start < 0:
        end, start = end - start, 0.0
    if end > duration:
        start, end = max(0.0, start - (end - duration)), duration
    return round(start, 2), round(end, 2)
