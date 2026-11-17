"""Find the most interesting moments of a long video.

Two scorers:
  * heuristic: loudness spikes (laughs, shouting, hype), excitement words, speech density,
    visual activity and a hook bonus. Always runs, needs no network.
  * Claude: reads the timestamped transcript (plus loudness peaks) and picks clips like an
    editor would. Used when credentials are available; heuristic score is blended in.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Callable

import numpy as np

from . import brain, hype
from .options import Options

LogFn = Callable[[str], None]

EXCITE_WORDS = {
    "wow", "omg", "whoa", "woah", "haha", "hahaha", "lol", "lmao", "crazy", "insane", "unbelievable",
    "amazing", "incredible", "awesome", "insane", "wild", "holy", "damn", "dude", "bro", "yes", "no",
    "what", "wait", "stop", "seriously", "literally", "never", "always", "best", "worst", "secret",
    "mistake", "truth", "actually", "shocking", "huge", "massive", "million", "billion", "money",
    "love", "hate", "funny", "hilarious", "scared", "terrifying", "dead", "died", "fail", "win",
    "won", "lost", "goal", "boom", "finally", "impossible", "story", "listen", "imagine", "why",
}
EXCITE_PHRASES = ["oh my god", "no way", "let's go", "lets go", "what the", "are you kidding",
                  "i can't believe", "you won't believe", "here's the thing", "the problem is",
                  "nobody talks about", "the secret", "pay attention", "this is why"]
LAUGH_RE = re.compile(r"\b(ha){2,}\b|\[(laugh|laughter|laughs|applause|cheering)\]|\(laughs?\)", re.I)


@dataclass
class Clip:
    start: float
    end: float
    title: str = ""
    hook: str = ""
    reason: str = ""
    category: str = ""
    score: float = 0.0
    heuristic: float = 0.0
    source: str = "heuristic"
    files: dict = field(default_factory=dict)
    offset: float = 0.0     # live: where this part's window starts in the whole recording
    part: str = ""          # live: which recorded part the clip came from

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration"] = round(self.duration, 2)
        d["abs_start"] = round(self.start + self.offset, 2)
        d["abs_end"] = round(self.end + self.offset, 2)
        return d


# --------------------------------------------------------------------------- timeline features

def _robust_z(x: np.ndarray) -> np.ndarray:
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826 + 1e-6
    return np.clip((x - med) / mad, -3, 5)


def _rank(values: np.ndarray) -> np.ndarray:
    """Percentile rank 0..1; equal values share their average rank (a flat feature gives no signal)."""
    if len(values) <= 1:
        return np.ones_like(values, dtype=np.float64)
    order = values.argsort(kind="stable")
    ranks = np.empty(len(values), dtype=np.float64)
    ranks[order] = np.arange(len(values))
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    sums = np.bincount(inverse, weights=ranks)
    ranks = sums[inverse] / counts[inverse]
    return ranks / (len(values) - 1)


class Timeline:
    def __init__(self, transcript: dict, loud_db: np.ndarray, scene: np.ndarray, hop: float, duration: float,
                 audio: dict | None = None):
        """audio: spectral features from hype.audio_features() (optional; loudness alone works too)."""
        self.hop = hop
        self.duration = duration
        n = int(np.ceil(duration / hop)) + 1
        self.n = n
        loud = np.full(n, float(np.median(loud_db)) if len(loud_db) else -60.0)
        m = min(n, len(loud_db))
        loud[:m] = loud_db[:m]
        self.loud_z = _robust_z(loud)
        self.scene = np.zeros(n)
        k = min(n, len(scene))
        self.scene[:k] = scene[:k]
        self.words = np.zeros(n)
        self.excite = np.zeros(n)
        self.segments = transcript.get("segments", [])
        self.all_words = [w for s in self.segments for w in s.get("words", [])]
        for w in self.all_words:
            i = min(n - 1, int(w["start"] / hop))
            self.words[i] += 1
            token = re.sub(r"[^\w']", "", w["word"].lower())
            if token in EXCITE_WORDS:
                self.excite[i] += 1
            if "!" in w["word"]:
                self.excite[i] += 1.5
            if "?" in w["word"]:
                self.excite[i] += 0.5
        for s in self.segments:
            text = s["text"].lower()
            hits = sum(text.count(p) for p in EXCITE_PHRASES) + 2 * len(LAUGH_RE.findall(text))
            if hits:
                i = min(n - 1, int(s["start"] / hop))
                self.excite[i] += 2 * hits
        # repeated shouts ("let's go let's go", "no no no") are hype, not filler
        for k in range(1, len(self.all_words)):
            a, b = self.all_words[k - 1], self.all_words[k]
            if re.sub(r"[^\w']", "", a["word"].lower()) == re.sub(r"[^\w']", "", b["word"].lower()) \
                    and b["start"] - a["end"] < 0.6:
                self.excite[min(n - 1, int(b["start"] / hop))] += 1.0
        self.hype, self.hype_parts = hype.hype_curve(loud, audio, self.excite, hop, self.words)
        self.events = hype.find_events(self.hype, self.hype_parts, hop)
        self.situation: dict | None = None  # filled by the local AI
        self._cs = {name: np.concatenate([[0], np.cumsum(getattr(self, name))])
                    for name in ("loud_z", "scene", "words", "excite", "hype")}
        self._cs["loud_pos"] = np.concatenate([[0], np.cumsum(np.clip(self.loud_z, 0, None) ** 2)])

    def event_in(self, a: float, b: float) -> "hype.HypeEvent | None":
        """Strongest hype event whose peak falls inside [a, b]."""
        inside = [e for e in self.events if a <= e.peak <= b]
        return max(inside, key=lambda e: e.strength) if inside else None

    def _sum(self, name: str, a: float, b: float) -> float:
        i, j = int(a / self.hop), max(int(a / self.hop) + 1, int(b / self.hop))
        i, j = max(0, min(i, self.n)), max(0, min(j, self.n))
        return float(self._cs[name][j] - self._cs[name][i])

    def features(self, a: float, b: float) -> np.ndarray:
        dur = max(b - a, 1e-3)
        i, j = int(a / self.hop), max(int(a / self.hop) + 1, int(b / self.hop))
        peak = float(np.max(self.loud_z[i:j])) if j > i else 0.0
        hook = self._sum("excite", a, a + 5) + 0.5 * self._sum("loud_pos", a, a + 3)
        return np.array([
            self._sum("loud_pos", a, b) / dur,     # sustained loudness above normal (hype/laughter)
            peak,                                  # biggest spike
            self._sum("excite", a, b) / dur,       # excitement words per second
            self._sum("words", a, b) / dur,        # speech density
            self._sum("scene", a, b) / dur,        # visual activity
            hook,                                  # strong opening
            self._sum("loud_pos", b, b + 4) / 4,   # reaction right after (laughter, applause, cheers)
            float(np.max(self.hype[i:j])) if j > i else 0.0,  # hype peak (shouting, crowd, action, spikes)
        ])

    def peaks(self, top: int = 40) -> list[tuple[float, float]]:
        """Loudest moments (seconds, z-score), spread apart by >= 10 s."""
        order = np.argsort(-self.loud_z)
        picked: list[tuple[float, float]] = []
        for i in order:
            t = i * self.hop
            if self.loud_z[i] < 2.0 or len(picked) >= top:
                break
            if all(abs(t - p) > 10 for p, _ in picked):
                picked.append((t, float(self.loud_z[i])))
        return sorted(picked)


#                  loud  spike words talk  scene hook  react hype
WEIGHTS = np.array([0.17, 0.08, 0.18, 0.06, 0.05, 0.10, 0.13, 0.23])
# "hype" mode: loud, intense moments first
WEIGHTS_HYPE = np.array([0.14, 0.12, 0.12, 0.02, 0.06, 0.05, 0.09, 0.40])


def _weights(opts: Options, tl: "Timeline | None" = None) -> np.ndarray:
    if getattr(opts, "hype_mode", "balanced") == "hype":
        return WEIGHTS_HYPE
    # when the local AI knows it's gameplay or a sports watch-along, loud moments matter most
    sit = getattr(tl, "situation", None) or {}
    if sit.get("video_type") in ("gaming", "sports_watch", "irl_stream"):
        return (WEIGHTS + WEIGHTS_HYPE) / 2
    return WEIGHTS


# --------------------------------------------------------------------------- boundary snapping

SENTENCE_END = re.compile(r"[.!?…]['\"”)]*$")
# A sentence that starts like this usually carries the point of the one before it.
CONTINUES = re.compile(r"^(and (so|the result|that's|that is|then)|so\b|which\b|that's why|that is why|because\b|"
                       r"but\b|and that\b|the (point|result|answer|reason)|therefore|meaning\b|then\b)", re.I)
REACTION_MAX = 6.0      # seconds of laughter/cheering kept after the last word
OVERSHOOT = 8.0         # how far past max_len a clip may run to finish its point


def _sentence_start(words: list[dict], i: int, limit: float) -> int:
    """Move back to the first word of the sentence containing word i (at most `limit` seconds)."""
    k = i
    while k > 0 and not SENTENCE_END.search(words[k - 1]["word"]) \
            and words[k]["start"] - words[k - 1]["end"] < 1.0 and words[i]["start"] - words[k - 1]["start"] <= limit:
        k -= 1
    return k


def _finish_point(words: list[dict], j: int, start: float, hard_end: float) -> int:
    """Extend word index j to the end of its sentence, then through follow-up sentences that
    complete the point ("...and the result is...", "so..."), staying before hard_end."""
    def fits(k: int) -> bool:
        return words[k]["end"] <= hard_end

    # 1) finish the current sentence
    while j + 1 < len(words) and not SENTENCE_END.search(words[j]["word"]) \
            and words[j + 1]["start"] - words[j]["end"] < 1.5 and fits(j + 1):
        j += 1
    # 2) take up to two follow-up sentences that continue the thought
    for _ in range(2):
        if j + 1 >= len(words):
            break
        gap = words[j + 1]["start"] - words[j]["end"]
        nxt = " ".join(w["word"] for w in words[j + 1:j + 5]).strip()
        # strong connector, or a plain "And ..." said without any pause
        if not ((gap <= 1.2 and CONTINUES.search(nxt)) or (gap <= 0.6 and re.match(r"^and\b", nxt, re.I))):
            break
        k = j + 1
        while k + 1 < len(words) and not SENTENCE_END.search(words[k]["word"]) \
                and words[k + 1]["start"] - words[k]["end"] < 1.5:
            k += 1
        if not fits(k):
            break
        j = k
    return j


def _reaction_tail(tl: Timeline, t: float, next_speech: float) -> float:
    """After the last word, keep going while the room is still loud (laughs, cheers, hype),
    until it quiets down, someone speaks again, or REACTION_MAX passes."""
    end = t
    quiet = 0.0
    while end - t < REACTION_MAX and end + tl.hop < next_speech - 0.15:
        i = int(end / tl.hop)
        if i >= tl.n:
            break
        if tl.loud_z[i] > 0.0:
            quiet = 0.0
        else:
            quiet += tl.hop
            if quiet >= 1.0:
                end -= quiet - tl.hop  # stop near where it went quiet
                break
        end += tl.hop
    return max(end, t)


def _snap(tl: Timeline, start: float, end: float, opts: Options) -> tuple[float, float]:
    """Align to word boundaries, start at a sentence start, end after the point and the reaction."""
    words = tl.all_words
    if words:
        starts = np.array([w["start"] for w in words])
        ends = np.array([w["end"] for w in words])
        i = int(np.searchsorted(starts, start - 0.6))
        i = min(i, len(words) - 1)
        i = _sentence_start(words, i, limit=8.0)
        start = max(0.0, starts[i] - 0.2)
        # last word that started before the requested end belongs to the clip
        j = int(np.searchsorted(starts, end + 0.2)) - 1
        j = max(j, i)
        end = ends[j] + 0.35
        # too long: cut at last sentence-ending word before max_len, else last word before max_len
        if end - start > opts.max_len:
            limit = start + opts.max_len
            cands = [k for k in range(i, len(words)) if ends[k] + 0.35 <= limit]
            if cands:
                sentence = [k for k in cands if re.search(r"[.!?…]$", words[k]["word"])
                            and ends[k] - start >= opts.min_len]
                k = sentence[-1] if sentence else cands[-1]
                end = ends[k] + 0.35
            else:
                end = limit
        # too short: extend over following words
        if end - start < opts.min_len:
            k = j
            while k + 1 < len(words) and ends[k + 1] + 0.35 - start <= opts.max_len and end - start < opts.min_len:
                k += 1
                end = ends[k] + 0.35
            if end - start < opts.min_len:
                end = start + opts.min_len
        # finish the point, then keep the reaction to it
        last = int(np.searchsorted(ends, end - 0.3, side="right")) - 1
        last = max(last, i)
        last = _finish_point(words, last, start, hard_end=start + opts.max_len + OVERSHOOT)
        hard_end = start + opts.max_len + OVERSHOOT
        next_speech = starts[last + 1] if last + 1 < len(words) else tl.duration
        tail = _reaction_tail(tl, ends[last], next_speech)
        # comic tag: a laugh, then a short follow-up line that gets its own laugh ("...Must try harder.")
        if tail - ends[last] >= 1.0 and last + 1 < len(words) and next_speech - tail <= 6.0 \
                and next_speech - ends[last] <= 10.0:
            k = last + 1
            while k + 1 < len(words) and not SENTENCE_END.search(words[k]["word"]) and k - last < 6:
                k += 1
            if k - last <= 6 and ends[k] <= hard_end:
                after = starts[k + 1] if k + 1 < len(words) else tl.duration
                tag_tail = _reaction_tail(tl, ends[k], after)
                if tag_tail - ends[k] >= 1.0:
                    last, next_speech, tail = k, after, tag_tail
        end = max(end, min(tail + 0.5, hard_end, next_speech - 0.05))
    else:
        dur = min(max(end - start, opts.min_len), opts.max_len)
        end = start + dur
    end = min(end, tl.duration)
    if end - start < min(opts.min_len, tl.duration):
        start = max(0.0, end - opts.min_len)
    return round(float(start), 3), round(float(end), 3)


# --------------------------------------------------------------------------- heuristic scorer

def _candidate_windows(tl: Timeline, opts: Options) -> list[tuple[float, float]]:
    wins: list[tuple[float, float]] = []
    segs = tl.segments
    speech_time = sum(s["end"] - s["start"] for s in segs)
    if segs and speech_time > 0.2 * tl.duration:
        starts = [s["start"] for s in segs]
        ends = [s["end"] for s in segs]
        for i in range(len(segs)):
            for j in range(i, len(segs)):
                d = ends[j] - starts[i]
                if d > opts.max_len:
                    break
                if d >= opts.min_len:
                    wins.append((starts[i], ends[j]))
    if not wins:  # music / gameplay / silent footage: fixed grid
        lengths = sorted({opts.min_len, (opts.min_len + opts.max_len) / 2})
        step = max(2.0, opts.min_len / 4)
        t = 0.0
        while t < tl.duration:
            for L in lengths:
                if t + L <= tl.duration:
                    wins.append((t, t + L))
            t += step
        if not wins:
            wins.append((0.0, tl.duration))
    return wins


def _hype_windows(tl: Timeline, opts: Options) -> list[tuple[float, float]]:
    """Clip ranges built around each hype event (set-up before it, reaction after), in a few lengths."""
    wins = []
    for ev in tl.events:
        for setup in (6.0, 12.0, 20.0):
            a, b = hype.event_window(ev, opts.min_len, opts.max_len, tl.duration, setup=setup)
            if b - a >= min(opts.min_len, tl.duration) - 0.5:
                wins.append((a, b))
    return sorted(set(wins))


def _windows_for(tl: Timeline, opts: Options) -> list[tuple[float, float]]:
    hype_wins = _hype_windows(tl, opts)
    if getattr(opts, "hype_mode", "balanced") == "hype" and hype_wins:
        return hype_wins
    return _candidate_windows(tl, opts) + hype_wins


def _pick_non_overlapping(scored: list[tuple[float, float, float]], k: int, gap: float = 2.0):
    picked: list[tuple[float, float, float]] = []
    for s, a, b in sorted(scored, key=lambda x: -x[0]):
        if all(b + gap <= pa or a >= pb + gap for _, pa, pb in picked):
            picked.append((s, a, b))
            if len(picked) >= k:
                break
    return picked


AllowFn = Callable[[float, float], bool]


def heuristic_clips(tl: Timeline, opts: Options, k: int, allow: AllowFn | None = None) -> list[Clip]:
    wins = _windows_for(tl, opts)
    if allow:
        wins = [w for w in wins if allow(*w)] or wins[:0]
    if not wins:
        return []
    feats = np.array([tl.features(a, b) for a, b in wins])
    ranks = np.column_stack([_rank(feats[:, c]) for c in range(feats.shape[1])])
    scores = ranks @ _weights(opts, tl)
    # mild preference for lengths near the middle of the allowed range
    target = (opts.min_len + opts.max_len) / 2
    durs = np.array([b - a for a, b in wins])
    scores = scores - 0.05 * np.abs(durs - target) / max(target, 1)
    picked = _pick_non_overlapping(list(zip(scores.tolist(), *zip(*wins))), k)
    index = {w: i for i, w in enumerate(wins)}
    clips = []
    for s, a, b in picked:
        r = ranks[index[(a, b)]]
        a, b = _snap(tl, a, b, opts)
        a, b = ensure_payoff(tl, a, b, opts, min(180.0, opts.max_len * 1.5))
        if allow and not allow(a, b):
            continue
        title, hook = _auto_title(tl, a, b)
        reason, category = _explain(tl, r, a, b)
        clips.append(Clip(start=a, end=b, score=round(100 * float(s), 1), heuristic=round(100 * float(s), 1),
                          title=title, hook=hook, reason=reason, category=category, source="heuristic"))
    return clips


FEATURE_TEXT = [
    "sustained loud reactions",
    "one of the loudest spikes in the video",
    "lots of hype words",
    "rapid back-and-forth talk",
    "a lot of on-screen action",
    "a strong first few seconds",
    "a big reaction right after (laughs, cheers)",
    "a hype peak",
]
EVENT_CATEGORY = {"shout": "hype", "crowd": "crowd reaction", "impacts": "action", "words": "hype",
                  "jump": "reaction", "loud": "reaction"}


def _explain(tl: Timeline, ranks: np.ndarray, a: float, b: float) -> tuple[str, str]:
    """Plain-language reason built from the signals that stand out for this window."""
    order = [i for i in np.argsort(-ranks) if ranks[i] >= 0.7][:3]
    if not order:
        order = [int(np.argmax(ranks))]
    parts = [FEATURE_TEXT[i] for i in order]
    ev = tl.event_in(a, b)
    if 7 in order:
        parts[order.index(7)] = (f"a hype peak: {ev.label} at {int(ev.peak // 60)}:{int(ev.peak % 60):02d}"
                                 if ev else "intense, loud audio")
    # quote the most excited lines actually spoken in the clip
    lines = []
    for s in tl.segments:
        if a <= s["start"] <= b:
            hits = tl._sum("excite", s["start"], s["end"] + 0.5)
            text = _collapse_repeats(s["text"]).strip(" ,.")
            if hits > 0 and text and len(text.split()) <= 7 and text.lower() not in (x.lower() for _, x in lines):
                lines.append((hits, text))
    lines.sort(key=lambda x: -x[0])
    if 2 in order and lines:
        parts[order.index(2)] += " (" + ", ".join(f"“{t}”" for _, t in lines[:2]) + ")"
    reason = "Stands out for " + ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] + "."
    reason = reason[0].upper() + reason[1:]
    top = order[0]
    category = {0: "reaction", 1: "reaction", 2: "hype", 3: "conversation", 4: "action", 5: "hook", 6: "funny",
                7: "hype"}[int(top)]
    if ev and (top == 7 or ranks[7] >= 0.85):  # a clear hype event names the clip's category
        category = EVENT_CATEGORY.get(ev.kind, category)
    return reason, category


def heuristic_score(tl: Timeline, clips: list[Clip], opts: Options) -> None:
    """Attach a 0-100 heuristic score to clips (percentile against random windows of the video)."""
    wins = _candidate_windows(tl, opts)
    if len(wins) > 3000:
        idx = np.random.default_rng(0).choice(len(wins), 3000, replace=False)
        wins = [wins[i] for i in idx]
    base_feats = np.array([tl.features(a, b) for a, b in wins])
    for c in clips:
        f = tl.features(c.start, c.end)
        w = _weights(opts, tl)
        pct = [(base_feats[:, i] <= f[i]).mean() for i in range(len(f))] if len(base_feats) else [0.5] * len(w)
        c.heuristic = round(100 * float(np.dot(pct, w)), 1)


def _collapse_repeats(text: str) -> str:
    """'Let's go Let's go let's go' -> "Let's go"; 'short, short, short' -> 'short'."""
    words = text.split()

    def norm(ws: list[str]) -> list[str]:
        return [re.sub(r"[^\w']", "", x.lower()) for x in ws]

    for size in range(1, 5):
        out, i = [], 0
        while i < len(words):
            chunk = norm(words[i:i + size])
            j = i + size
            while len(chunk) == size and j + size <= len(words) and norm(words[j:j + size]) == chunk:
                j += size
            if j > i + size:  # found repeats: keep one copy, with the last copy's punctuation
                out.extend(words[i:i + size - 1] + [words[j - 1]])
                i = j
            else:
                out.append(words[i])
                i += 1
        words = out
    return " ".join(words)


def _auto_title(tl: Timeline, a: float, b: float) -> tuple[str, str]:
    """Title = the clip's key spoken sentence (the one that gets the biggest reaction, or the most
    excited one), cleaned up; hook = a short version for the on-screen title box."""
    words = [w for w in tl.all_words if a <= w["start"] <= b]
    if not words:
        stamp = f"{int(a // 60)}:{int(a % 60):02d}"
        return f"Highlight at {stamp}", ""

    # split the clip into sentences: punctuation, or a pause (unpunctuated callouts in gameplay)
    sentences: list[list[dict]] = [[]]
    for k, w in enumerate(words):
        sentences[-1].append(w)
        pause = k + 1 < len(words) and words[k + 1]["start"] - w["end"] > 0.7
        if SENTENCE_END.search(w["word"]) or pause:
            sentences.append([])
    sentences = [s for s in sentences if s]

    def score(s: list[dict]) -> float:
        t0, t1 = s[0]["start"], s[-1]["end"]
        excite = tl._sum("excite", t0, t1 + 0.5)
        loud = tl._sum("loud_pos", t0, t1)
        reaction = tl._sum("loud_pos", t1, t1 + 3)          # the line people laughed/cheered at
        n = len(s)
        fit = 1.0 if 4 <= n <= 16 else 0.4                  # readable as a title
        question = 0.5 if s[-1]["word"].endswith("?") else 0.0
        return (2 * excite + loud + 2 * reaction + question + 0.05 * n) * fit

    best = max(sentences, key=score)
    text = _clean_line(_join_words(best))
    if len(text.split()) < 3:  # tiny shout: add the next sentence for context
        idx = sentences.index(best)
        if idx + 1 < len(sentences):
            text = f"{text.rstrip('.!?')}! {_clean_line(_join_words(sentences[idx + 1]))}"
    title = _tail_clauses(text, 80)  # keep the end: that's where the punchline/point is
    hook = _shorten(title, 62, max_words=14)  # fits the 3-line title box on shorts
    return title, hook


def _join_words(ws: list[dict]) -> str:
    """Join Whisper word tokens with natural spacing ("T" + "-shirt" -> "T-shirt", "don" + "'t" -> "don't")."""
    out = ""
    for w in ws:
        token = w["word"]
        if out and (re.match(r"^[-'’,.!?%)\]]", token) or out.endswith(("-", "(", "["))):
            out += token
        else:
            out += (" " if out else "") + token
    return out


def _tail_clauses(text: str, max_chars: int) -> str:
    """If text is too long, drop leading clauses (set-up) and keep the ending; else shorten normally."""
    if len(text) <= max_chars:
        return text
    clauses = re.split(r"(?<=[,;:—])\s+", text)
    for k in range(1, len(clauses)):
        tail = " ".join(clauses[k:])
        if len(tail) <= max_chars and len(tail.split()) >= 4:
            return tail[:1].upper() + tail[1:]
    return _shorten(text, max_chars)


FILLER_RE = re.compile(r"\b(um+|uh+|erm+|uh-huh)\b[,.]?\s*|,?\s*\byou know\b,?|,?\s*\bI mean\b,?", re.I)
LEAD_RE = re.compile(r"^(and|so|but|well|anyway|okay|ok|now|because|then|like|yeah|oh)\b[,!.]?\s+", re.I)


def _clean_line(text: str) -> str:
    """Remove filler and leading connectors, collapse stutters, tidy punctuation and case."""
    text = FILLER_RE.sub(" ", text)
    text = _collapse_repeats(re.sub(r"\s+", " ", text).strip())
    for _ in range(3):
        text = LEAD_RE.sub("", text)
    text = re.sub(r"\s+([,.!?])", r"\1", text).strip(" ,;:-–—")
    return text[:1].upper() + text[1:] if text else text


def _shorten(text: str, max_chars: int, max_words: int = 99) -> str:
    """Fit text into max_chars/max_words, preferring to stop at a clause break over cutting mid-phrase."""
    words = text.split()
    if len(text) <= max_chars and len(words) <= max_words:
        return text
    out = ""
    best_clause = ""
    for w in words[:max_words]:
        cand = f"{out} {w}".strip()
        if len(cand) > max_chars:
            break
        out = cand
        if re.search(r"[,;:—?!.]$", w) and len(out.split()) >= 3:
            best_clause = out
    if best_clause and len(best_clause) >= 0.5 * len(out):
        return best_clause.rstrip(",;:—")
    return out.rstrip(",;:—") + "…"


# --------------------------------------------------------------------------- Claude scorer

SYSTEM_PROMPT = """You are a senior short-form video editor who finds viral moments in long videos \
(podcasts, streams, vlogs, lectures, interviews, gameplay) for TikTok, YouTube Shorts and Reels.

You get a timestamped transcript (seconds) and a list of audio loudness spikes, which often mark \
laughter, shouting, applause or hype. Choose self-contained moments that a viewer with no context would \
enjoy: funny exchanges, surprising reveals, strong opinions, emotional beats, useful insights, \
dramatic reactions, great stories with a payoff.

Rules:
- Each clip must start at the beginning of a thought (a strong hook in the first 3 seconds, no \
mid-sentence starts) and end after the payoff or punchline. Do not cut a sentence in half.
- Use timestamps that appear in the transcript. Respect the requested min/max length.
- Clips must not overlap. Prefer variety across the whole video.
- title: catchy, max 60 characters, in the language of the video, no hashtags.
- hook: on-screen hook text, max 6 words.
- score: 0-100 estimate of how likely the clip is to perform well as a short.
- Order clips by score, best first."""

CLIP_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "reason": {"type": "string"},
                    "category": {"type": "string"},
                    "score": {"type": "integer"},
                },
                "required": ["start", "end", "title", "hook", "reason", "category", "score"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}

CHUNK_CHARS = 350_000


def _transcript_lines(segments: list[dict]) -> list[str]:
    return [f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments if s["text"]]


def _call_claude(client, opts: Options, user_text: str, log: LogFn) -> list[dict]:
    import anthropic

    params = dict(
        model=opts.llm_model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_text}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": CLIP_SCHEMA}},
    )
    try:
        resp = client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params)
    except (TypeError, anthropic.BadRequestError) as exc:
        if "fallback" not in str(exc).lower() and not isinstance(exc, TypeError):
            raise
        resp = client.messages.create(**params)
    if resp.stop_reason == "refusal":
        log("Claude declined to analyze this transcript; using heuristic picks.")
        return []
    if resp.stop_reason == "max_tokens":
        log("Claude response was cut off; using heuristic picks for this part.")
        return []
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    return json.loads(text).get("clips", []) if text else []


def claude_clips(tl: Timeline, opts: Options, k: int, log: LogFn) -> list[Clip]:
    import anthropic

    lines = _transcript_lines(tl.segments)
    if not lines:
        return []
    client = anthropic.Anthropic(max_retries=3)
    peaks = ", ".join(f"{t:.0f}s(+{z:.1f})" for t, z in tl.peaks())
    events = "; ".join(f"{e.peak:.0f}s {e.label} (strength {e.strength:.1f}, builds from {e.start:.0f}s)"
                       for e in sorted(tl.events, key=lambda e: -e.strength)[:40])
    focus = ("\nPriority: the user wants HYPE clips. Pick the loudest, most intense moments first (hype events "
             "below), with enough set-up before the peak to understand it and the reaction after it.\n"
             if getattr(opts, "hype_mode", "balanced") == "hype" else "")

    chunks, cur, size = [], [], 0
    for line in lines:
        if size + len(line) > CHUNK_CHARS and cur:
            chunks.append(cur)
            cur, size = cur[-20:], sum(len(x) for x in cur[-20:])  # small overlap
        cur.append(line)
        size += len(line) + 1
    chunks.append(cur)

    raw: list[dict] = []
    for n, chunk in enumerate(chunks, 1):
        want = k if len(chunks) == 1 else max(2, int(np.ceil(k * 1.5 / len(chunks))) + 1)
        log(f"Asking Claude for highlights (part {n}/{len(chunks)})")
        user = (
            f"Video length: {tl.duration:.0f} seconds.\n"
            f"Find the {want + 2} best clips, each between {opts.min_len:.0f} and {opts.max_len:.0f} seconds long.\n"
            f"Loudness spikes (time(+strength)): {peaks or 'none'}\n"
            f"Hype events detected in the audio (shouting, crowd reactions, action bursts, sudden spikes): "
            f"{events or 'none'}\n{focus}\n"
            "Transcript:\n" + "\n".join(chunk)
        )
        raw.extend(_call_claude(client, opts, user, log))

    clips = []
    for r in raw:
        try:
            a, b = float(r["start"]), float(r["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= a < b <= tl.duration + 1):
            continue
        a, b = _snap(tl, a, b, opts)
        clips.append(Clip(start=a, end=b, title=str(r.get("title", ""))[:80], hook=str(r.get("hook", ""))[:60],
                          reason=str(r.get("reason", "")), category=str(r.get("category", "")),
                          score=float(r.get("score", 50)), source="claude"))
    return clips


# --------------------------------------------------------------------------- entry point

def _clip_transcript(tl: Timeline, a: float, b: float, before: float = 15.0, after: float = 45.0) -> str:
    """Timestamped lines from a bit before a to well after b (outcomes land there), marked by side."""
    rows = []
    for s in tl.segments:
        if a - before <= s["start"] <= b + after and s["text"].strip():
            where = "before" if s["start"] < a else "after" if s["start"] > b else "clip"
            rows.append(f"[{s['start']:.0f}] ({where}) {s['text'].strip()}")
    for e in tl.events:
        if a - before <= e.peak <= b + after:
            rows.append(f"[{e.peak:.0f}] (AUDIO: {e.label})")
    return "\n".join(sorted(rows, key=lambda r: float(r[1:r.index("]")]))) or "(no speech)"


def ensure_payoff(tl: Timeline, a: float, b: float, opts: Options, max_len: float) -> tuple[float, float]:
    """Safety net: a reaction right after the end (a cheer, a laugh, a burst of 'oh my god', 'yes!') means the
    clip stopped before its outcome. Extend through it, finishing the sentence, within max_len."""
    from dataclasses import replace

    hard = min(tl.duration, a + max_len)
    new_b = b
    for e in sorted(tl.events, key=lambda e: e.peak):
        if b - 1.0 < e.peak <= b + 20.0 and e.peak + 1.5 <= hard:
            new_b = max(new_b, min(hard, e.end + 1.0))
    excited = [w for w in tl.all_words if b < w["start"] <= b + 12.0
               and (re.sub(r"[^\w']", "", w["word"].lower()) in EXCITE_WORDS or "!" in w["word"])]
    if len(excited) >= 3 and excited[-1]["end"] + 1.0 <= hard:
        new_b = max(new_b, excited[-1]["end"] + 1.0)
    if new_b <= b + 0.5:
        return a, b
    return _snap(tl, a, new_b, replace(opts, max_len=max_len))


AUTO_MIN_SCORE = 60  # automatic clip count: the AI's score a moment needs to become a clip


def brain_clips(tl: Timeline, opts: Options, k: int, log: LogFn, title: str, video: str | None = None) -> list[Clip]:
    """k = 0: automatic, every moment good enough to post becomes a clip."""
    try:
        return _brain_clips(tl, opts, k, log, title, video)
    finally:
        # free the models' memory before rendering: a 14B model spilled into RAM left too little for the
        # video frames (OpenCV "Insufficient memory")
        for m in brain.installed():
            brain.unload(m)


def _brain_clips(tl: Timeline, opts: Options, k: int, log: LogFn, title: str, video: str | None = None) -> list[Clip]:
    """Local AI (Ollama), free and offline:
    1. situation: what kind of video, what's going on
    2. events:    the video split into its commotions (challenges, stories, plays...), each read on its own for
                  what happens, its climax and its set-up; sponsor reads are found and skipped. Guided by the
                  user's past 👍/👎
    3. hype:      strongest audio peaks join as candidates (silent plays still get a look)
    4. eyes:      a vision model looks at frames of each candidate (kills, banners, faces, scores)
    5. review:    a strict second read of each clip on its own: fix start/end, rewrite title, drop weak ones
    """
    from dataclasses import replace

    from . import feedback, media

    model = brain.pick_model(opts.brain_model)
    ok, msg = brain.available(model)
    if not ok:
        log(msg + " Using the offline scorer.")
        return []
    if opts.brain_model in ("", "auto"):
        log(f"Local AI: using {model} (best fit for this PC's GPU)")
    elif model != opts.brain_model:
        log(f"{opts.brain_model} isn't downloaded; using {model}.")
    votes = feedback.load()

    # 1. situation
    sit = brain.situation(tl.segments, title, tl.duration, tl.events, model, log)
    tl.situation = sit
    vtype = sit.get("video_type", "other")
    who = ", ".join(sit.get("people") or [])
    log(f"Looks like a {vtype.replace('_', ' ')} video"
        + (f" ({sit['game_or_topic']})" if sit.get("game_or_topic") else "")
        + (f" with {who}" if who else "") + f": {sit.get('summary', '')}")
    taste = brain.taste_text(votes, vtype)
    if taste:
        log(f"Using your {sum(1 for v in votes.values())} clip ratings to guide the picks")
    _, (lo, hi) = brain.TYPE_GUIDE.get(vtype, brain.TYPE_GUIDE["other"])
    lo, hi = max(opts.min_len, lo), min(opts.max_len, hi)
    if lo >= hi:
        lo, hi = opts.min_len, opts.max_len
    sub = replace(opts, min_len=lo, max_len=hi)  # type-specific clip length
    long_max = min(180.0, max(hi, opts.max_len) * 1.5)  # allowed when the climax needs it
    sub_long = replace(opts, min_len=lo, max_len=long_max)

    # 2. understand the video as a series of events (each commotion), and clip whole events
    cands: list[dict] = []
    events = brain.event_map(tl.segments, sit, tl.events, tl.duration, opts, model, log, taste=taste)
    tl.event_map = events
    if events:
        log(f"Local AI: mapped {len(events)} events: "
            + "; ".join(f"{int(e['start'] // 60)}:{int(e['start'] % 60):02d} {e['title'][:30]}" for e in events[:6])
            + ("…" if len(events) > 6 else ""))
    ads = [(e["start"], e["end"]) for e in events if e.get("ad")]
    if ads:
        log("Skipping sponsor reads: " + ", ".join(f"{int(a // 60)}:{int(a % 60):02d}-{int(b // 60)}:{int(b % 60):02d}"
                                                   for a, b in ads))

    def in_ad(a: float, b: float) -> bool:
        return sum(max(0.0, min(b, y) - max(a, x)) for x, y in ads) > 0.3 * (b - a)

    n_beats = 0
    for e in events:
        if e.get("ad"):
            continue
        a, b, cx = e["start"], e["end"], e["climax"]
        cx_end = max(cx, e.get("climax_end", cx))
        if b - a > max(hi * 1.3, lo + 20):
            # a long event (a whole challenge with several attempts, a long story): every moment in it that
            # works on its own becomes a candidate, each from its set-up to after its payoff and the reaction
            log(f"Local AI: finding the moments inside “{e['title'][:40]}”")
            moments = brain.beats(tl.segments, e, sit, model, log, lo, hi, taste=taste)
            good = [m for m in moments if m["score"] >= 40]
            if len(good) >= 2 or (good and b - a > long_max):
                for m in good:
                    mb = min(e["end"], max(m["end"], m["payoff_end"] + 2.0))
                    ma = max(e["start"], min(m["start"], mb - lo))  # too short: more set-up before it
                    ma = max(ma, mb - long_max)  # too long: less set-up, never less outcome
                    ma, mb = _snap(tl, ma, mb, sub_long)
                    mb = min(e["end"], max(mb, m["payoff_end"] + 1.0))
                    cands.append({"start": ma, "end": mb, "title": m["title"], "hook": m["hook"],
                                  "why": m["what_happens"], "kind": m["kind"], "climax": m["payoff"],
                                  "score": float(m["score"]), "event": True, "limit": e["end"],
                                  "floor": e["start"], "climax_end": m["payoff_end"]})
                n_beats += len(good)
                continue  # its moments stand for the event: more clips, each ending on its own payoff
        if b - a > long_max:
            # too long for a short: start where the decisive attempt is set up, keep the whole outcome after it
            b = min(b, cx_end + 12.0)
            a = max(e["start"], min(e.get("setup", a), cx - 8.0))
            a = max(a, b - long_max)
            if b - a < lo:
                a = max(e["start"], b - lo)
        want_end = b
        a, b = _snap(tl, a, b, sub_long)
        b = max(b, min(want_end, a + long_max))  # snapping must not cut the end of the event off
        if cx and b < cx + 2:  # snapping must not cut the climax off
            a, b = _snap(tl, a, min(e["end"], cx + 6), sub_long)
        cands.append({"start": a, "end": b, "title": str(e.get("title", ""))[:80], "hook": str(e.get("hook", ""))[:60],
                      "why": str(e.get("what_happens", "")), "kind": str(e.get("kind", "")), "climax": cx,
                      "score": float(max(0, min(100, e.get("score", 50)))), "event": True,
                      "limit": e["end"], "floor": e["start"],
                      "climax_end": cx_end})
    auto_k = max(3, int(tl.duration // 150))  # how many to look for when the count is automatic
    raw = [] if events else brain.moments(tl.segments, sit, tl.events, tl.duration, opts, k or auto_k, model, log,
                                          taste=taste)
    for m in raw:
        climax = m.get("climax")
        a, b = _snap(tl, m["start"], m["end"], sub_long if m["end"] - m["start"] > hi else sub)
        if climax and b < climax + 2:  # snapping must not cut the climax off
            a, b = _snap(tl, a, climax + 4, sub_long)
        cands.append({"start": a, "end": b, "title": str(m.get("title", ""))[:80], "hook": str(m.get("hook", ""))[:60],
                      "why": str(m.get("why", "")), "kind": str(m.get("kind", "")), "climax": climax,
                      "score": float(max(0, min(100, m.get("score", 50))))})
    # 3. strongest hype peaks the transcript pass didn't cover
    for ev in sorted(tl.events, key=lambda e: -e.strength)[:k or auto_k]:
        if any(c["start"] <= ev.peak <= c["end"] for c in cands):
            continue
        if any(e["start"] <= ev.peak < e["end"] for e in events):  # inside an understood event: the event is the clip
            continue
        a, b = _snap(tl, *hype.event_window(ev, sub.min_len, sub.max_len, tl.duration), sub)
        if in_ad(a, b):
            continue
        t_, h_ = _auto_title(tl, a, b)
        cands.append({"start": a, "end": b, "title": t_, "hook": h_, "why": f"{ev.label} in the audio",
                      "kind": EVENT_CATEGORY.get(ev.kind, "hype"), "score": 55.0})
    n_ai = sum(1 for c in cands if c.get("event")) or len(raw)
    log(f"Local AI: {n_ai} {'events and moments' if events else 'moments'} to choose from"
        + (f" ({n_beats} found inside long events)" if n_beats else "")
        + f", {len(cands) - n_ai} more from hype peaks")

    # rank before the slower passes
    shells = [Clip(start=c["start"], end=c["end"]) for c in cands]
    heuristic_score(tl, shells, opts)
    for c, s in zip(cands, shells):
        c["heur"] = s.heuristic
        c["rank"] = 0.7 * c["score"] + 0.3 * s.heuristic
    cands.sort(key=lambda c: -c["rank"])
    # one topic split in two (the set-up picked separately from the attempt): merge neighbours into one clip
    merged: list[dict] = []
    for c in sorted(cands, key=lambda c: c["start"]):
        prev = merged[-1] if merged else None
        both_events = bool(prev and prev.get("event") and c.get("event"))  # whole events stay separate
        if prev and not both_events and c["start"] - prev["end"] <= 12.0 \
                and max(c["end"], prev["end"]) - prev["start"] <= long_max:
            best = prev if prev["rank"] >= c["rank"] else c
            merged[-1] = {**best, "start": prev["start"], "end": max(prev["end"], c["end"]),
                          "rank": max(prev["rank"], c["rank"]), "score": max(prev["score"], c["score"]),
                          "climax": max([x for x in (prev.get("climax"), c.get("climax")) if x] or [None],
                                        key=lambda v: v or 0)}
        else:
            merged.append(c)
    cands = sorted(merged, key=lambda c: -c["rank"])
    dedup: list[dict] = []
    for c in cands:
        if all(c["end"] + 0.5 <= d["start"] or c["start"] >= d["end"] + 0.5 for d in dedup):
            dedup.append(c)
    if k:
        cands = dedup[: max(k * 2, k + 3)]
    else:  # automatic: every moment good enough to post
        good = [c for c in dedup if c["score"] >= AUTO_MIN_SCORE]
        cands = good if len(good) >= 3 else dedup[:3]
        log(f"Local AI: {len(cands)} moments are good enough to be clips")

    # 4. eyes (unload the text model first: both together don't fit an 8 GB GPU)
    vision_model = brain.pick_vision_model(model)
    if opts.use_vision and video and vision_model in brain.installed():
        brain.unload(model)
        for i, c in enumerate(cands, 1):
            log(f"Local AI: looking at moment {i} of {len(cands)}")
            span = c["end"] - c["start"]
            ev = tl.event_in(c["start"], c["end"])
            times = [c["start"] + span * f for f in (0.2, 0.55, 0.9)]
            if ev:
                times[1] = ev.peak
            topic = sit.get("game_or_topic") or ""
            context = f"It is from a {vtype.replace('_', ' ')} video" + (f" about {topic}." if topic else ".")
            try:
                frames = media.grab_frames(video, sorted(times), width=512)
                vis = brain.see(frames, context, model=vision_model)
            except (brain.BrainError, OSError) as exc:
                # one stuck or failed call usually means the rest would fail too: stop looking, keep going
                log(f"Skipping the look at frames for this job ({str(exc)[:100]})")
                break
            c["vision"] = vis
            c["banner"] = brain.banner_bonus(vis)
            c["rank"] += 0.25 * (float(vis.get("visual_score", 50)) - 50) + c["banner"]
    elif opts.use_vision and video:
        log(f"Vision model {vision_model} isn't downloaded; skipping the look at frames.")

    # 5. editor review
    brain.unload(vision_model)
    cands.sort(key=lambda c: -c["rank"])
    out: list[Clip] = []
    to_review = cands[: max(k + 3, int(k * 1.6))] if opts.use_review and k else cands
    for i, c in enumerate(to_review, 1):
        if opts.use_review:
            log(f"Local AI: reviewing clip {i} of {len(to_review)}")
            try:
                r = brain.review(c, _clip_transcript(tl, c["start"], c["end"]), sit, taste, model, lo, hi)
            except brain.BrainError as exc:
                log(f"Review skipped: {str(exc)[:100]}")
                r = {}
            if r:
                # small models say keep=false too eagerly (even on clips they score 85); only a low score drops a clip
                review_score = float(max(0, min(100, r.get("score", 50))))
                if not r.get("keep", True) and review_score < 35:
                    c["rejected"] = True
                    c["review_score"] = review_score
                try:
                    ra, rb = float(r["start"]), float(r["end"])
                    if c.get("climax"):  # the review may move the end, but never before the climax
                        rb = max(rb, c["climax"] + 2)
                    # starts may move ±25 s; ends may move earlier by 25 s or later by 45 s (to reach the outcome)
                    # whole events end where the next one starts: the review may only trim a little off the end
                    min_end_shift = -10 if c.get("event") else -25
                    if abs(ra - c["start"]) <= 25 and min_end_shift <= rb - c["end"] <= 45 and rb - ra >= 5:
                        c["start"], c["end"] = _snap(tl, ra, rb, sub_long if rb - ra > hi else sub)
                except (KeyError, TypeError, ValueError):
                    pass
                if not c.get("event"):  # an event's title and summary come from reading the whole event
                    c["title"] = str(r.get("title") or c["title"])[:80]
                    c["why"] = str(r.get("why") or c["why"])
                c["hook"] = str(r.get("hook") or c["hook"])[:60]
                c["score"] = 0.5 * c["score"] + 0.5 * review_score
        if c.get("rejected"):
            continue
        c["start"], c["end"] = ensure_payoff(tl, c["start"], c["end"], opts, long_max)
        if c.get("limit"):  # a whole event doesn't run on into the next one, and always contains its climax
            c["end"] = min(max(c["end"], c.get("climax_end", 0) + 4), c["limit"])
            c["start"] = max(c["start"], c["end"] - long_max)
        # ending check, last so nothing cuts the outcome off again: does the clip show how its commotion turns
        # out? If the result only comes after the cut (a boundary a few lines early), end after it instead
        try:
            ends = [brain.finish(tl.segments, c["start"], c["end"], c["why"] or c["title"], model),
                    brain.outcome_after(tl.segments, c["start"], c["end"])]  # result words, no model needed
            new_end = max([e for e in ends if e] or [0.0])
            if new_end and c["end"] < new_end <= c["end"] + 45:
                c["start"] = max(c["start"], new_end - long_max)  # too long: less set-up, never less outcome
                c["end"] = new_end
                c["resolved"] = True
        except (brain.BrainError, TypeError, ValueError) as exc:
            log(f"Ending check skipped: {str(exc)[:100]}")
        c["end"] = brain.sentence_end(tl.segments, c["end"])  # never stop mid-sentence
        for x, y in ads:  # never into a sponsor read
            if c["start"] < x < c["end"]:
                c["end"] = x
            elif c["start"] < y < c["end"]:
                c["start"] = y
        if c.get("floor") is not None:  # a whole event doesn't start with the end of the one before
            c["start"] = max(c["start"], c["floor"])
        if c["end"] - c["start"] < lo:  # cut short by an ad or the next event: add set-up before it
            floor = max([y for x, y in ads if y <= c["start"] + 0.5] or [0.0])
            c["start"] = max(floor, c["end"] - lo)
        if c["end"] - c["start"] < 0.75 * lo:
            continue
        out.append(_brain_clip(c, votes, vtype))
    dropped = [c for c in to_review if c.get("rejected")]
    if len(out) < (k or 3) and dropped:  # never end up short: bring back the best of the dropped ones
        for c in sorted(dropped, key=lambda c: -c["rank"])[: (k or 3) - len(out)]:
            out.append(_brain_clip(c, votes, vtype))
            c["rejected"] = False
    ext = sum(1 for c in to_review if c.get("resolved"))
    if ext:
        log(f"Extended {ext} clip{'s' if ext != 1 else ''} so the outcome is included")
    n = sum(1 for c in to_review if c.get("rejected"))
    if n:
        log(f"Editor review dropped {n} weak clip{'s' if n != 1 else ''}")
    return out


def _brain_clip(c: dict, votes: dict, vtype: str) -> Clip:
    """Final score: the AI's judgment, measured hype/reactions, what the eyes saw, and your taste."""
    vis = c.get("vision") or {}
    bias = brain.taste_bias(votes, vtype, c["kind"])
    final = (0.6 * c["score"] + 0.25 * c["heur"] + 0.15 * float(vis.get("visual_score", 50)) + bias
             + c.get("banner", 0.0))
    reason = c["why"]
    shown = " ".join((vis.get("on_screen_text") or "").split())
    if shown and c.get("banner"):
        reason += f" On screen: “{shown[:60]}”."
    return Clip(start=c["start"], end=c["end"], title=c["title"], hook=c["hook"], reason=reason,
                category=c["kind"], score=round(final, 1), heuristic=c["heur"], source="local-ai")


def find_highlights(tl: Timeline, opts: Options, log: LogFn, allow: AllowFn | None = None,
                    title: str = "", video: str | None = None) -> list[Clip]:
    """allow(start, end) -> bool limits where clips may be (live parts: only new material, no repeats)."""
    k = opts.num_clips
    clips: list[Clip] = []
    from .env import claude_configured

    if opts.use_llm and tl.segments and claude_configured():
        pass  # Claude below
    elif opts.use_brain and tl.segments:
        try:
            clips = brain_clips(tl, opts, k, log, title, video)
        except brain.BrainError as exc:
            log(f"Local AI unavailable ({str(exc)[:160]}). Using the offline scorer.")
            clips = []
    elif opts.use_llm and tl.segments:
        log("Picking moments with the free offline scorer.")
    if opts.use_llm and tl.segments and claude_configured():
        try:
            clips = claude_clips(tl, opts, k or max(3, int(tl.duration // 150)), log)
            if clips:
                heuristic_score(tl, clips, opts)
                for c in clips:
                    c.score = round(0.8 * c.score + 0.2 * c.heuristic, 1)
        except Exception as exc:  # no credentials, network, etc.
            log(f"Claude analysis unavailable ({type(exc).__name__}: {str(exc)[:160]}). Using heuristic scorer.")
            clips = []
    if allow:
        clips = [c for c in clips if allow(c.start, c.end)]
    ai_found = bool(clips)
    limit = k or 10_000  # 0 = automatic: as many as are good
    # best first; a moment found twice (overlapping reading windows) or overlapping a better one is dropped
    final: list[Clip] = []
    for c in sorted(clips, key=lambda c: -c.score):
        if len(final) >= limit:
            break
        if all(c.end + 0.5 <= f.start or c.start >= f.end + 0.5 for f in final):
            final.append(c)
    if ai_found:
        if k and len(final) < k:
            # never pad with blind picks: they don't know where a moment ends, so they get cut off
            log(f"Found {len(final)} complete moments worth clipping (you asked for {k}).")
    else:  # no AI: the offline scorer picks
        n = k or max(3, int(tl.duration // 120))
        picks = heuristic_clips(tl, opts, n * 3, allow)
        best = max((h.score for h in picks), default=0)
        for h in picks:
            if len(final) >= n:
                break
            if not k and h.score < 0.6 * best:  # automatic: only the ones close to the best
                continue
            if all(h.end + 0.5 <= c.start or h.start >= c.end + 0.5 for c in final):
                final.append(h)
    final.sort(key=lambda c: -c.score)
    return final[:limit]


# --------------------------------------------------------------------------- pacing: dead air and punch-ins

def dead_air(tl: Timeline, a: float, b: float, min_gap: float = 0.7) -> list[tuple[float, float]]:
    """Ranges to KEEP (relative to a) so pauses with no words and nothing audible shrink to ~0.25 s.
    A gap with loud sound (laughs, cheers, game action) or a hype event in it is kept: that's a reaction."""
    words = [w for w in tl.all_words if a <= w["start"] < b]
    keep: list[tuple[float, float]] = []
    cur = a
    for w1, w2 in zip(words, words[1:]):
        g0, g1 = w1["end"], w2["start"]
        if g1 - g0 < min_gap:
            continue
        i, j = int(g0 / tl.hop), max(int(g0 / tl.hop) + 1, int(g1 / tl.hop))
        seg = tl.loud_z[i:j]
        if seg.size and float(np.max(seg)) > 0.5:
            continue
        if any(g0 <= e.peak <= g1 for e in tl.events):
            continue
        keep.append((cur, g0 + 0.12))
        cur = g1 - 0.13
    keep.append((cur, b))
    return [(round(x - a, 3), round(y - a, 3)) for x, y in keep if y - x > 0.05]


def remap(t: float, keep: list[tuple[float, float]]) -> float | None:
    """Clip-relative time -> time in the tightened clip (None if it falls in a removed gap)."""
    done = 0.0
    for x, y in keep:
        if t < x:
            return None
        if t <= y:
            return done + (t - x)
        done += y - x
    return None


def punch_ins(tl: Timeline, a: float, b: float, keep: list[tuple[float, float]]) -> list[tuple[float, float, float]]:
    """Pattern interrupts for shorts: (start, end, zoom) on the tightened timeline.
    A hard cut to ~108% at sentence starts every 3-5 s (alternating), and a stronger 116% punch on hype peaks."""
    total = sum(y - x for x, y in keep)
    out: list[tuple[float, float, float]] = []
    for e in tl.events:
        if a <= e.peak <= b:
            t = remap(e.peak - a, keep)
            if t is not None:
                out.append((float(max(0.0, t - 0.25)), float(min(total, t + 1.4)), 1.16))
    words = [w for w in tl.all_words if a <= w["start"] < b]
    starts = [words[0]["start"]] if words else []
    for w1, w2 in zip(words, words[1:]):
        if SENTENCE_END.search(w1["word"]) or w2["start"] - w1["end"] > 0.5:
            starts.append(w2["start"])
    last, zoomed = -10.0, False
    marks = []
    for s in starts:
        t = remap(s - a, keep)
        if t is not None and t - last >= 3.0:
            marks.append(t)
            last = t
    if len(marks) < 2 and total > 8:  # little or no speech: interrupt on a steady rhythm instead
        marks = [x * 4.0 for x in range(int(total // 4.0) + 1)]
    # keep a change at least every ~6 s: fill long stretches with cuts on word starts about 4 s apart
    word_ts = [t for t in (remap(w["start"] - a, keep) for w in words) if t is not None]
    filled: list[float] = []
    for k, t in enumerate(marks):
        filled.append(t)
        nxt = marks[k + 1] if k + 1 < len(marks) else total
        target = t + 4.0
        while nxt - target > 2.0:
            near = min(word_ts, key=lambda x: abs(x - target)) if word_ts else target
            if near - filled[-1] < 2.5 or nxt - near < 2.0:
                near = target
            filled.append(near)
            target = near + 4.0
    marks = filled
    for k, t in enumerate(marks):
        zoomed = not zoomed
        t_end = marks[k + 1] if k + 1 < len(marks) else total
        if zoomed and t_end - t > 0.4:
            if not any(s0 < t_end and t < s1 for s0, s1, _ in out):  # hype punches take priority
                out.append((round(t, 2), round(t_end, 2), 1.08))
    return sorted(out)


def ready_clip(tl: Timeline, opts: Options, log: LogFn, title: str = "") -> Clip:
    """The user's own finished clip: keep all of it (no cutting), and write its title, hook and summary."""
    clip = Clip(start=0.0, end=tl.duration, source="ready", score=100.0)
    clip.title, clip.hook = _auto_title(tl, 0.0, tl.duration)
    clip.reason = ""
    # where the clip came from says what it is when nobody talks (a clip cut from a live stream is named
    # "<stream title>: clip 2 (1:05-1:40)"; a file name like "match point.mp4" works too)
    base = re.sub(r":\s*clip \d+ \(.*\)$", "", title or "").strip()
    base = re.sub(r"\s*\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?$", "", base)  # YouTube adds the date to live titles
    base = re.sub(r"\.(mp4|mov|mkv|webm|ts|flv|avi|m4v)$", "", base, flags=re.IGNORECASE).replace("_", " ").strip()
    if base and not tl.all_words:
        clip.title = _shorten(base, 60) if len(base) > 60 else base
    if opts.use_brain and tl.segments:
        model = brain.pick_model(opts.brain_model)
        ok, msg = brain.available(model)
        if not ok:
            log(msg + " Writing the title from the transcript instead.")
            return clip
        try:
            log("Local AI: working out what this clip is about")
            sit = brain.situation(tl.segments, title, tl.duration, tl.events, model, log)
            tl.situation = sit
            res = brain.headline(tl.segments, sit, model)
            clip.title = str(res.get("title") or clip.title)[:80]
            # the hook is burned into the video, and the caption font has no emoji: drop them
            clip.hook = re.sub(r"[\U00010000-\U0010FFFF☀-➿️]", "", str(res.get("hook") or clip.hook))[:60].strip()
            clip.reason = str(res.get("what_happens") or "")
            clip.category = str(res.get("kind") or "")
            clip.source = "local-ai"
            log(f"Title: {clip.title}")
        except brain.BrainError as exc:
            log(f"Local AI unavailable ({str(exc)[:120]}). Using a title from the transcript.")
        finally:
            for m in brain.installed():  # free the GPU and RAM for rendering
                brain.unload(m)
    elif not tl.segments:
        log("No speech found, so there are no captions. The title is a placeholder: edit it before uploading.")
    return clip
