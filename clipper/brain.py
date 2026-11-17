"""Local video understanding with an Ollama model running on this computer (free, offline).

Two passes over the transcript:
  1. situation: what kind of video this is, what's going on, who is in it, what makes a good clip here
  2. moments:   read the transcript in ~8 minute windows (with detected hype marked in) and pick moments the way an
                editor would: a joke with its set-up, a story with its payoff, an argument, a clutch play with
                the reaction after it. Each moment says where the set-up starts and where the payoff ends.
"""
from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request
from typing import Callable

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = "qwen2.5:7b"
SMART_MODEL = "qwen2.5:14b"
ADVANCED_MODEL = "qwen3.8:27b"
VISION_MODEL = "qwen2.5vl:7b"
LogFn = Callable[[str], None]

VIDEO_TYPES = ["gaming", "podcast", "irl_stream", "talk", "challenge", "sports_watch", "news", "vlog", "music",
               "other"]
KINDS = ["joke", "story", "argument", "reveal", "insight", "emotional", "reaction", "clutch", "fail", "hype", "other"]
# labels that make sense per video type (offering all of them makes small models repeat one)
TYPE_KINDS = {
    "gaming": ["clutch", "fail", "funny", "reaction", "hype", "banter"],
    "sports_watch": ["goal", "big_play", "reaction", "controversy", "celebration", "funny"],
    "challenge": ["attempt", "win", "fail", "funny", "reaction", "twist"],
    "irl_stream": ["funny", "surprise", "interaction", "reaction", "emotional", "story"],
    "podcast": ["story", "joke", "opinion", "argument", "insight", "reveal"],
    "talk": ["joke", "story", "insight", "emotional", "reveal"],
    "news": ["statement", "reveal", "exchange", "emotional"],
}

# What a good clip looks like for each kind of video, and the length it usually needs.
TYPE_GUIDE = {
    "gaming": ("Clip plays and reactions: clutches, multi-kills, close rounds, funny fails, rage or hype moments, "
               "good banter. Start a few seconds before the play begins so viewers understand it; end after the "
               "reaction, not on the kill.", (18, 40)),
    "podcast": ("Clip complete stories, strong opinions, funny exchanges, surprising facts and heated moments. "
                "Start where the question or story starts; end after the punchline, answer or conclusion.", (30, 60)),
    "irl_stream": ("Clip surprising things that happen, funny interactions with people, reactions to events and "
                   "chat, and emotional moments. Include what caused the reaction.", (20, 50)),
    "talk": ("Clip one clear idea, joke or story with its set-up and payoff. A clip must make sense on its own "
             "and end on a complete thought or a laugh.", (30, 60)),
    "challenge": ("Challenge, competition or game-show video: clip one attempt or round from the set-up (what's at "
                  "stake) through the attempt to the RESULT (made it or failed, who won, the money) and the reaction. "
                  "A clip that stops before the result is pointless.", (30, 75)),
    "sports_watch": ("Clip goals, big plays, controversial calls and the final whistle with the reaction. Start just "
                     "before the play; always include how it turned out and the celebration or anger after it.",
                     (20, 60)),
    "news": ("Clip one complete statement, revelation or exchange. No half sentences; include who is speaking "
             "and the key line.", (20, 50)),
    "vlog": ("Clip funny, surprising or emotional moments and short complete stories.", (20, 50)),
    "music": ("Clip the strongest, most energetic part of a song or performance, or a funny moment between songs.",
              (15, 45)),
    "other": ("Clip self-contained moments that would make a stranger stop scrolling: funny, surprising, "
              "emotional, insightful or intense.", (20, 55)),
}

# Short-form hook rules: the first ~3 s decide stay-or-swipe; most people watch muted, so the hook is on-screen text.
HOOK_GUIDE = ("hook: the on-screen text for the first seconds, max 30 characters, no emojis, no hashtags. It must open "
              "a curiosity gap the clip then closes (never give the answer away), lead with the most interesting "
              "element, use a specific detail or number when there is one, and stay true to what happens (no "
              "clickbait the clip doesn't deliver). Good shapes: open loop (\"He didn't see this coming\"), stakes "
              "(\"1 HP, 3 enemies left\"), negation (\"Stop doing this in ranked\"), the unexpected (\"Then the "
              "teacher said this\"), cautionary tale (\"This mistake cost the round\").")
START_GUIDE = ("The first 3 seconds decide if people keep watching: begin on something that grabs attention or opens "
               "a question (a strong line, the start of the action), never on throat-clearing, greetings or dead air.")

SITUATION_SCHEMA = {
    "type": "object",
    "properties": {
        "video_type": {"type": "string", "enum": VIDEO_TYPES},
        "summary": {"type": "string"},
        "people": {"type": "array", "items": {"type": "string"}},
        "game_or_topic": {"type": "string"},
        "language": {"type": "string"},
        "good_moments": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["video_type", "summary", "people", "game_or_topic", "language", "good_moments"],
}

MOMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "setup_start": {"type": "number"},
                    "start": {"type": "number"},
                    "climax": {"type": "number"},
                    "end": {"type": "number"},
                    "kind": {"type": "string", "enum": KINDS},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "why": {"type": "string"},
                    "score": {"type": "integer"},
                },
                "required": ["setup_start", "start", "climax", "end", "kind", "title", "hook", "why", "score"],
            },
        }
    },
    "required": ["moments"],
}


class BrainError(RuntimeError):
    pass


# --------------------------------------------------------------------------- Ollama

def _request(path: str, payload: dict | None = None, timeout: float = 5.0) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(OLLAMA + path, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise BrainError(f"Ollama error {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise BrainError(f"Ollama isn't reachable at {OLLAMA} ({exc})") from exc


def installed() -> set[str]:
    try:
        tags = _request("/api/tags", timeout=3)
    except BrainError:
        return set()
    names = set()
    for m in tags.get("models", []):
        n = m.get("name", "")
        names.add(n)
        names.add(n.removesuffix(":latest"))
    return names


def gpu_memory_mb() -> int:
    import subprocess
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return int(out.strip().splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return 0


def pick_model(wanted: str) -> str:
    """"auto": smart 14B when the GPU can hold it (12 GB+), else fast 7B. Falls back to what's downloaded."""
    have = installed()
    if wanted in ("", "auto"):
        wanted = SMART_MODEL if gpu_memory_mb() >= 12000 else DEFAULT_MODEL
    # Advanced models are opt-in: installing one must not change the automatic default.
    if wanted == ADVANCED_MODEL and "qwen3.8:latest" in have:
        return "qwen3.8:latest"
    for m in (wanted, DEFAULT_MODEL, SMART_MODEL):
        if m in have:
            return m
    return wanted


def unload(model: str) -> None:
    """Free the model's GPU memory now (so the next model gets the whole GPU)."""
    try:
        _request("/api/generate", {"model": model, "keep_alive": 0}, timeout=30)
    except BrainError:
        pass


def pick_vision_model(text_model: str) -> str:
    """Qwen 3.8 handles both transcript and frames when explicitly selected."""
    return text_model if text_model.startswith("qwen3.8:") else VISION_MODEL


def available(model: str = DEFAULT_MODEL) -> tuple[bool, str]:
    """(ready, message)."""
    try:
        tags = _request("/api/tags", timeout=3)
    except BrainError:
        return False, "Ollama isn't running. Start the Ollama app to let Clipper understand videos."
    names = {m.get("name", "") for m in tags.get("models", [])}
    if model in names or f"{model}:latest" in names:
        return True, f"Local AI ready ({model})."
    return False, f"The model {model} isn't downloaded yet. Run: ollama pull {model}"


def _chat(model: str, system: str, user: str, schema: dict, num_ctx: int = 8192, timeout: float = 600,
          images: list[bytes] | None = None, max_tokens: int = 1500) -> dict:
    import base64

    msg = {"role": "user", "content": user}
    if images:
        msg["images"] = [base64.b64encode(b).decode("ascii") for b in images]
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, msg],
        "stream": False,
        "format": schema,
        "keep_alive": "3m",  # free the GPU a few minutes after the last call
        # cap the answer and discourage loops (small models can repeat a phrase until they run out of room)
        "options": {"temperature": 0.2, "num_ctx": num_ctx, "num_predict": max_tokens, "repeat_penalty": 1.15},
    }
    if model.startswith("qwen3.8:"):
        # These tasks require short schema-constrained JSON, not a reasoning trace.
        payload["think"] = False
    try:
        resp = _request("/api/chat", payload, timeout=timeout)
    except BrainError as exc:  # the model server can crash under memory pressure; free memory and retry once
        if "500" not in str(exc) and "terminated" not in str(exc):
            raise
        for m in installed():
            unload(m)
        import time
        time.sleep(2)
        resp = _request("/api/chat", payload, timeout=timeout)
    text = (resp.get("message") or {}).get("content", "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise BrainError("The local model returned something that isn't valid JSON.") from exc


# --------------------------------------------------------------------------- transcript formatting

def _lines(segments: list[dict], t0: float = 0.0, t1: float = math.inf) -> list[tuple[float, str]]:
    return [(s["start"], s["text"].strip()) for s in segments if t0 <= s["start"] < t1 and s["text"].strip()]


def _fmt(lines: list[tuple[float, str]], events: list = ()) -> str:
    rows = [(t, f"[{t:.0f}] {text}") for t, text in lines]
    rows += [(e.peak, f"[{e.peak:.0f}] (AUDIO: {e.label}, strength {e.strength:.1f})") for e in events]
    rows.sort(key=lambda r: r[0])
    return "\n".join(r for _, r in rows)


def _sample(segments: list[dict], budget_words: int = 1800) -> str:
    """Beginning, middle and end of the transcript within a word budget."""
    lines = _lines(segments)
    if not lines:
        return "(no speech)"
    words = sum(len(t.split()) for _, t in lines)
    if words <= budget_words:
        return _fmt(lines)
    third = budget_words // 3
    picked, out = [], []
    for start in (0, len(lines) // 2, max(0, len(lines) - 1)):
        n, i, part = 0, start, []
        step = -1 if start == len(lines) - 1 else 1
        while 0 <= i < len(lines) and n < third:
            part.append(lines[i])
            n += len(lines[i][1].split())
            i += step
        picked.extend(sorted(part))
    seen = set()
    for row in sorted(picked):
        if row[0] not in seen:
            seen.add(row[0])
            out.append(row)
    return _fmt(out)


# --------------------------------------------------------------------------- passes

SYSTEM = ("You are an experienced short-form video editor. You read transcripts of long videos and understand "
          "what is happening: who is talking, what the situation is, what is funny, tense, surprising or "
          "important, and where a moment really starts and ends. Timestamps in [brackets] are seconds from the "
          "start of the video. Lines marked (AUDIO: ...) are sounds detected in the audio, such as crowd "
          "reactions, shouting or action bursts. Answer only with the requested JSON.")


def situation(segments: list[dict], title: str, duration: float, events: list, model: str, log: LogFn) -> dict:
    kinds: dict[str, int] = {}
    for e in events:
        kinds[e.label] = kinds.get(e.label, 0) + 1
    hype = ", ".join(f"{v}x {k}" for k, v in kinds.items()) or "none"
    speech = sum(s["end"] - s["start"] for s in segments)
    user = (f"Video title or file name: {title or 'unknown'}\n"
            f"Length: {duration / 60:.0f} minutes. Speech covers {100 * speech / max(duration, 1):.0f}% of it.\n"
            f"Loud moments detected in the audio: {hype}\n\n"
            f"Transcript excerpts (beginning, middle, end):\n{_sample(segments)}\n\n"
            "What kind of video is this, what is going on, who is in it (names if said), which game or topic, "
            "which language, and what kinds of moments in THIS video would make great short clips?\n"
            "video_type meanings: gaming = someone playing a VIDEO game (on a screen); challenge = real-life "
            "challenges, competitions, bets or game shows (people attempting things for money or a prize); "
            "sports_watch = watching or commentating a real sports match; podcast = people talking at a table "
            "or on microphones; irl_stream = a streamer out in the real world with a camera; talk = one person "
            "presenting or lecturing; news = news or reporting; vlog = someone filming their day; music = songs or "
            "performances; other = anything else.")
    log("Local AI: working out what this video is")
    return _chat(model, SYSTEM, user, SITUATION_SCHEMA)


def moments(segments: list[dict], sit: dict, events: list, duration: float, opts, k: int, model: str,
            log: LogFn, window: float = 480.0, overlap: float = 60.0, taste: str = "") -> list[dict]:
    vtype = sit.get("video_type", "other")
    guide, (lo, hi) = TYPE_GUIDE.get(vtype, TYPE_GUIDE["other"])
    lo, hi = max(opts.min_len, lo), min(opts.max_len, hi)
    if lo >= hi:
        lo, hi = opts.min_len, opts.max_len
    hype_first = getattr(opts, "hype_mode", "balanced") == "hype"
    n_windows = max(1, math.ceil(max(duration - overlap, 1) / (window - overlap)))
    per = max(2, math.ceil(k * 1.6 / n_windows) + 1)
    out: list[dict] = []
    t = 0.0
    idx = 0
    while t < duration:
        idx += 1
        t1 = min(duration, t + window)
        lines = _lines(segments, t, t1)
        evs = [e for e in events if t <= e.peak < t1]
        # what happens next (outcomes often land just past the edge of the part)
        ahead = _lines(segments, t1, min(duration, t1 + 90))
        ahead_evs = [e for e in events if t1 <= e.peak < t1 + 90]
        if lines or evs:
            log(f"Local AI: reading part {idx} of {n_windows} ({t / 60:.0f}-{t1 / 60:.0f} min)")
            user = (
                f"About this video: {sit.get('summary', '')}\n"
                f"Type: {vtype}. People: {', '.join(sit.get('people') or []) or 'unknown'}. "
                f"Game/topic: {sit.get('game_or_topic') or 'unknown'}.\n"
                f"Good clips here: {guide}\n"
                + (f"{taste}\n" if taste else "")
                + ("PRIORITY: the user wants HYPE clips: the loudest, most intense moments first (AUDIO lines).\n"
                   if hype_first else "")
                + f"\nFind up to {per} of the best moments in this part ({t:.0f}s to {t1:.0f}s). For each one:\n"
                f"- setup_start: where the context begins (the question, the start of the story, the start of the "
                f"play), so a stranger understands it\n"
                f"- start: where the clip should begin (same as setup_start or a little later). {START_GUIDE}\n"
                f"- climax: the second the moment pays off: the result of the attempt (made it, missed, who won), "
                f"the punchline, the answer, the reveal or the conclusion of the topic. Every clip MUST contain its "
                f"climax: a clip that stops before it is pointless. If the climax happens in the 'what happens "
                f"next' lines, use that time.\n"
                f"- end: after the climax AND the reaction to it (the laugh, the cheer, the 'oh my god'), when the "
                f"topic is finished; never mid-sentence and never before the climax\n"
                f"- clips should be about {lo:.0f} to {hi:.0f} seconds; if the climax needs more, make it longer "
                f"(up to {min(180, hi * 1.5):.0f} s) or start later, but never cut the climax\n"
                f"- title: catchy, max 60 characters, the main keyword (game, person or topic) early, in the "
                f"video's language, no hashtags\n"
                f"- {HOOK_GUIDE}\n"
                f"- why: one sentence on what happens\n"
                f"- score: 0-100, how well it would do as a short\n"
                f"Skip intros, ads, sponsor reads and dead air. If nothing in this part is good, return fewer.\n\n"
                f"Transcript:\n{_fmt(lines, evs)}"
                + (f"\n\nWhat happens next (after this part; only for finding climaxes and ends of moments that "
                   f"start above):\n{_fmt(ahead, ahead_evs)}" if ahead or ahead_evs else "")
            )
            schema = json.loads(json.dumps(MOMENT_SCHEMA))
            kinds = TYPE_KINDS.get(vtype, ["funny", "story", "surprise", "emotional", "insight", "reaction"])
            schema["properties"]["moments"]["items"]["properties"]["kind"]["enum"] = kinds
            user += f"\n\nkind must be one of: {', '.join(kinds)}. Pick the one that fits each moment best."
            try:
                res = _chat(model, SYSTEM, user, schema)
            except BrainError as exc:
                log(f"Local AI skipped part {idx}: {exc}")
                res = {"moments": []}
            for m in res.get("moments", []):
                try:
                    a, b = float(m["start"]), float(m["end"])
                    s0 = float(m.get("setup_start", a))
                    cx = float(m.get("climax", b))
                except (KeyError, TypeError, ValueError):
                    continue
                if not (t - 30 <= a < t1) or b - a < 3 or b > t1 + 100:
                    continue
                if s0 < a and a - s0 <= 25:  # include the set-up when it's close
                    a = s0
                if a < cx <= a + 180 and b < cx + 2:  # the end must come after the climax
                    b = cx + 4
                out.append({**m, "start": a, "end": b, "climax": cx if a < cx <= b else None,
                            "_lo": lo, "_hi": hi})
        if t1 >= duration:
            break
        t = t1 - overlap
    return out


# --------------------------------------------------------------------------- eyes: look at the moment

VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "scene": {"type": "string"},
        "events": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "on_screen_text": {"type": "string"},
        "emotion": {"type": "string"},
        "visual_score": {"type": "integer"},
    },
    "required": ["scene", "events", "on_screen_text", "emotion", "visual_score"],
}

BANNERS = ("ace", "clutch", "victory", "won", "win", "eliminated", "defeat", "flawless", "thrifty", "team ace",
           "double kill", "triple kill", "quadra", "penta", "multi kill", "headshot", "goal", "winner", "champion",
           "game over", "level up", "new record", "you died", "wasted", "mission passed")


def banner_bonus(vision: dict) -> float:
    """Big on-screen moments (round won, ACE, VICTORY, kill streaks, goals) are strong evidence."""
    text = " ".join([vision.get("on_screen_text", "")] + list(vision.get("events") or [])).lower()
    hits = sum(1 for b in BANNERS if f" {b} " in f" {text} ")
    return min(15.0, 6.0 * hits)


def see(frames: list[bytes], context: str, model: str = VISION_MODEL) -> dict:
    """Describe what happens on screen across a few frames of one moment (in time order)."""
    user = (f"These {len(frames)} frames are in time order from one moment of a video. {context}\n"
            "Base everything only on what you can see in these frames. Describe what is on screen. List visible events (for games: kills, kill feed, banners like ACE, "
            "CLUTCH, VICTORY, DEFEAT, round or score changes, deaths; for people: reactions, faces, gestures; "
            "for sports: goals, replays, celebrations). Copy important on-screen text. Say what emotion it "
            "shows. visual_score 0-100: how visually exciting or meaningful this moment is as a short clip "
            "(0 = nothing happens, 100 = huge moment).")
    res = _chat(model, "You describe video frames precisely and briefly. Describe only what is visible; never "
                       "guess events you can't see. Answer only with the requested JSON.",
                user, VISION_SCHEMA, num_ctx=8192, images=frames, timeout=120, max_tokens=350)
    seen, events = set(), []
    for e in res.get("events") or []:
        key = " ".join(str(e).lower().split())
        if key and key not in seen:
            seen.add(key)
            events.append(str(e).strip())
    res["events"] = events[:5]
    return res


# --------------------------------------------------------------------------- editor review

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "keep": {"type": "boolean"},
        "start": {"type": "number"},
        "end": {"type": "number"},
        "title": {"type": "string"},
        "hook": {"type": "string"},
        "why": {"type": "string"},
        "score": {"type": "integer"},
    },
    "required": ["keep", "start", "end", "title", "hook", "why", "score"],
}


RESOLVE_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string"},
        "resolved_inside": {"type": "boolean"},
        "resolution_time": {"type": "number"},
        "resolution_line": {"type": "string"},
    },
    "required": ["question", "resolved_inside", "resolution_time", "resolution_line"],
}


def resolve(transcript: str, model: str) -> dict:
    """One focused question (small models do far better with one job): what does this clip set up, and is it
    resolved inside the clip? If not, when is it resolved in the lines after it?"""
    user = (
        "Read this clip from a video. Lines marked (clip) are inside the clip; lines marked (after) come right "
        "after it ends. Numbers in [brackets] are seconds.\n\n"
        f"{transcript}\n\n"
        "Every good clip opens a question that viewers wait to see answered, for example: will he hit the "
        "bullseye, which burger does the judge pick, does she make it across the beam, what is the punchline.\n"
        "1. Write that question for THIS clip in your own words (not these instructions).\n"
        "2. Is it answered in the (clip) lines? Only say yes if the result is actually shown or said there.\n"
        "3. If not: find the (after) line where it is answered (the shot result, the winner, the verdict, the "
        "punchline). If the (after) lines move on to a different topic before answering, use the last (after) "
        "line that still belongs to this topic.\n\n"
        "Example answer for a clip that stops before the result: {\"question\": \"Will Jimmy hit the bullseye?\", "
        "\"resolved_inside\": false, \"resolution_time\": 131, \"resolution_line\": \"He hit it! That's the "
        "bullseye!\"}")
    return _chat(model, SYSTEM, user, RESOLVE_SCHEMA, max_tokens=300)


def review(clip: dict, transcript: str, sit: dict, taste: str, model: str, lo: float, hi: float) -> dict:
    """A second look at one clip on its own: does it make sense to a stranger, where should it really start/end."""
    vision = clip.get("vision") or {}
    seen = ""
    if vision:
        seen = (f"\nOn screen: {vision.get('scene', '')}. Events: {', '.join(vision.get('events') or []) or 'none'}. "
                f"Text: {vision.get('on_screen_text', '') or 'none'}. Emotion: {vision.get('emotion', '')}.")
    user = (
        f"Video: {sit.get('summary', '')} (type: {sit.get('video_type', 'other')})\n"
        f"Proposed clip: {clip['start']:.0f}s to {clip['end']:.0f}s, \"{clip.get('title', '')}\" "
        f"({clip.get('kind', '')}): {clip.get('why', '')}{seen}\n"
        f"{taste}\n"
        "Transcript around the clip (lines before the proposed start and after the proposed end are context):\n"
        f"{transcript}\n\n"
        "Review it like a strict editor. Only state things the transcript or the on-screen notes show; never "
        "invent events (who shot whom, kills, wins) that aren't there.\n"
        "- keep: false if a stranger would not get it or it isn't actually interesting, funny, tense or useful\n"
        "- start: the second where the set-up begins (move earlier if context is missing, later if it drags). "
        f"{START_GUIDE}\n"
        "- end: FIRST check the climax is inside the clip: the result of the attempt, the punchline, the answer, "
        "the conclusion of the topic. If it happens in the (after) lines, move the end to include it and the "
        "reaction to it: a clip without its climax is pointless. Then end right after that reaction, when the "
        "topic is finished; never mid-sentence\n"
        f"- the clip should be about {lo:.0f} to {hi:.0f} seconds, longer (up to {min(180, hi * 1.5):.0f} s) only "
        "when the climax needs it\n"
        "- title: max 60 characters, the main keyword (game, person or topic) early, true to what happens\n"
        f"- {HOOK_GUIDE}\n"
        "- why: one sentence describing what happens in the clip for a viewer (not your critique)\n"
        "- score 0-100 after your review")
    return _chat(model, SYSTEM, user, REVIEW_SCHEMA)


# --------------------------------------------------------------------------- taste: learn from 👍/👎

def taste_text(store: dict, video_type: str, limit: int = 5) -> str:
    """Few-shot examples from the user's votes, same video type first."""
    items = sorted(store.values(), key=lambda v: (v.get("video_type") != video_type, -v.get("t", 0)))
    liked = [v for v in items if v.get("vote") == "up"][:limit]
    disliked = [v for v in items if v.get("vote") == "down"][:limit]
    if not liked and not disliked:
        return ""
    parts = ["The user rated earlier clips. Pick moments like the liked ones and avoid ones like the disliked ones."]
    for name, rows in (("Liked", liked), ("Disliked", disliked)):
        for v in rows:
            parts.append(f"{name}: [{v.get('video_type', '')}/{v.get('kind', '')}] \"{v.get('title', '')}\": "
                         f"{v.get('why', '')} | said: {v.get('text', '')[:160]}")
    return "\n".join(parts)


def taste_bias(store: dict, video_type: str, kind: str) -> float:
    """Score nudge (-12..+12) from how often the user liked this kind of clip in this kind of video."""
    votes = [v["vote"] for v in store.values()
             if v.get("kind") == kind and v.get("video_type") in (video_type, None, "")]
    if not votes:
        return 0.0
    up = votes.count("up")
    down = votes.count("down")
    return max(-12.0, min(12.0, 12.0 * (up - down) / (up + down + 2)))


# --------------------------------------------------------------------------- event map: understand, then clip whole

SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"first_line": {"type": "integer"}, "last_line": {"type": "integer"},
                               "about": {"type": "string"}, "ad": {"type": "boolean"}},
                "required": ["first_line", "last_line", "about", "ad"],
            },
        }
    },
    "required": ["sections"],
}

EVENT_SCHEMA = {
    "type": "object",
    "properties": {
        "what_happens": {"type": "string"},
        "continues_previous": {"type": "boolean"},
        "ad": {"type": "boolean"},
        "climax_line": {"type": "integer"},
        "setup_line": {"type": "integer"},
        "new_thing_line": {"type": "integer"},
        "kind": {"type": "string"},
        "title": {"type": "string"},
        "hook": {"type": "string"},
        "score": {"type": "integer"},
    },
    "required": ["what_happens", "continues_previous", "ad", "climax_line", "setup_line", "new_thing_line", "kind",
                 "title", "hook", "score"],
}

AD_SCHEMA_ONE = {
    "type": "object",
    "properties": {"line": {"type": "integer"}},
    "required": ["line"],
}

AD_SCHEMA = {
    "type": "object",
    "properties": {"first_line": {"type": "integer"}, "last_line": {"type": "integer"}},
    "required": ["first_line", "last_line"],
}

# phrases that give a sponsor read away; a hit makes Clipper check the section for an ad
AD_WORDS = re.compile(
    r"sponsor|link in (the )?description|promo code|use (my )?code|free trial|% off|brought to you by|"
    r"partnered with|start your (own )?(store|business)|download (it|the app)|sign up (today|now|for free)",
    re.IGNORECASE)


def _numbered(rows: list[dict], i0: int, i1: int) -> str:
    """Lines numbered from 1 within this excerpt (small numbers a model can't mix up with seconds)."""
    return "\n".join(f"{i - i0 + 1}: {rows[i]['text']}" for i in range(i0, i1))


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ad_span(rows: list[dict], a: int, b: int, model: str) -> tuple[int, int] | None:
    """First and last line (indexes into rows) of the sponsor read inside rows[a:b], or None if there isn't one."""
    i1 = min(b, a + 150)
    user = ("These transcript lines may contain a sponsor read, ad or plug for a product, with normal video before "
            "or after it. Give the number of the FIRST line of the ad (where the talk turns to the product) and the "
            "LAST line of the ad (after it the normal video continues). If there is no ad, return 0 for both.\n\n"
            f"Lines:\n{_numbered(rows, a, i1)}")
    try:
        r = _chat(model, SYSTEM, user, AD_SCHEMA, max_tokens=60)
    except BrainError:
        return None
    f, last = _as_int(r.get("first_line"), 0), _as_int(r.get("last_line"), 0)
    if f < 1 or last < f or last > i1 - a:
        return None
    return _grow_ad(rows, a + f - 1, a + last - 1)


def _grow_ad(rows: list[dict], f: int, last: int) -> tuple[int, int]:
    """Widen an ad to every nearby line that names the product (the model tends to find only its middle)."""
    from collections import Counter

    names = Counter(w.strip(".,!?'\"").lower() for i in range(f, last + 1) for w in rows[i]["text"].split()
                    if w[:1].isupper() and len(w) > 3)
    for w in ("this", "that", "there", "what", "when", "whether", "every", "with", "your", "they", "just", "step",
              "like", "because", "jimmy", "here", "okay", "yeah"):
        names.pop(w, None)
    brand = next((w for w, c in names.most_common(1) if c >= 2), None)
    if not brand:
        return f, last
    hit = lambda i: brand in rows[i]["text"].lower() or bool(AD_WORDS.search(rows[i]["text"]))  # noqa: E731
    i = f - 1
    while i >= max(0, f - 12):  # brand lines up to 3 lines apart belong to the same read
        if hit(i):
            f = i
        elif f - i > 3:
            break
        i -= 1
    if f > 0 and rows[f]["start"] - rows[f - 1]["start"] <= 3:  # the lead-in line ("so, while you wait...")
        f -= 1
    i = last + 1
    while i < min(len(rows), last + 13):
        if hit(i):
            last = i
        elif i - last > 3:
            break
        i += 1
    return f, last


def event_map(segments: list[dict], sit: dict, audio_events: list, duration: float, opts, model: str,
              log: LogFn, taste: str = "", window: int = 70, overlap: int = 20) -> list[dict]:
    """Understand the video as a series of events (each commotion: a challenge, a round, a story, an argument,
    a sponsor read), in order, and what each one is about.

    Small, grounded steps, so even a 7B model gets them right:
    1. sections: read numbered transcript lines and split them into sections (a new challenge, topic, ad...)
    2. events:   read each section on its own and say what happens, whether something new starts inside it
                 (then it is split and both parts are read again), which line is the climax (the result, the
                 punchline) and where the build-up to it starts
    3. ads:      sections that look like a sponsor read get their exact first and last ad line, so the ad is cut
                 out of the events around it
    Lines are referred to by number, never by guessed seconds. An event ends where the next one begins, so a
    clip of an event contains the whole commotion. Returns events with start, setup, climax, climax_end, end,
    title, hook, what_happens, kind, score and ad."""
    rows = [{"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()}
            for s in segments if s["text"].strip()]
    if not rows:
        return []
    vtype = sit.get("video_type", "other")
    guide, _ = TYPE_GUIDE.get(vtype, TYPE_GUIDE["other"])
    kinds = TYPE_KINDS.get(vtype, ["funny", "story", "surprise", "emotional", "insight", "reaction"])
    hype_first = getattr(opts, "hype_mode", "balanced") == "hype"
    about = (f"About this video: {sit.get('summary', '')}\nType: {vtype}. People: "
             f"{', '.join(sit.get('people') or []) or 'unknown'}. "
             f"Game/topic: {sit.get('game_or_topic') or 'unknown'}.")
    n = len(rows)

    # 1. sections: two passes with windows shifted by half, so a boundary one pass misses (or sees only at the edge
    # of its window) the other catches
    starts: dict[int, dict] = {0: {"about": "", "ad": False}}  # first line -> what the section is, looks like an ad
    step = window - overlap
    offsets = [0] + ([step // 2] if n > window else [])
    jobs = [(o, i0) for o in offsets for i0 in range(o, n, step) if i0 == o or i0 + overlap < n]
    for w, (o, i0) in enumerate(jobs, 1):
        i1 = min(n, i0 + window)
        log(f"Local AI: mapping events {w} of {len(jobs)} "
            f"({rows[i0]['start'] / 60:.0f}-{rows[i1 - 1]['end'] / 60:.0f} min)")
        user = (
            f"{about}\n\nSplit these transcript lines into consecutive SECTIONS that together cover every line, in "
            "order, with no gaps. A new section begins exactly where something new starts: a new challenge, game, "
            "round or opponent; a new topic, story or question; a sponsor read or ad starts; the video comes back "
            "from an ad. One challenge with all its attempts, its result and the reaction to it is ONE section.\n"
            "For each section: first_line, last_line, about = what it is, ad = true for a sponsor read, ad or "
            "plug.\n\n"
            f"Lines:\n{_numbered(rows, i0, i1)}"
        )
        try:
            res = _chat(model, SYSTEM, user, SECTION_SCHEMA, max_tokens=1200)
        except BrainError as exc:
            log(f"Local AI skipped part {w}: {exc}")
            continue
        for s in res.get("sections", []):
            li = i0 + _as_int(s.get("first_line"), 0) - 1
            # boundaries right at the edges of a window are unsure: it can't see what's around them
            if i0 + (3 if i0 else 0) <= li < i1 - (0 if i1 == n else 5):
                prev = starts.get(li, {"about": "", "ad": False})
                starts[li] = {"about": prev["about"] or str(s.get("about", "")), "ad": prev["ad"] or bool(s.get("ad"))}
    # boundaries a few lines apart are the same one: keep the first
    first: list[int] = []
    for li in sorted(starts):
        if first and (li - first[-1] < 4 or rows[li]["start"] - rows[first[-1]]["start"] < 10):
            continue
        first.append(li)
    # put each boundary on the exact line: the aftermath (result, reactions, thanks) stays with what came before
    bounds = [0]
    for j, li in enumerate(first[1:], 1):
        lo_, hi_ = max(bounds[-1] + 2, li - 12), min(n, li + 13)
        before = starts.get(first[j - 1], {}).get("about") or "the previous part"
        after = starts.get(li, {}).get("about") or "something new"
        user = (f"{about}\n\nIn these transcript lines the video moves from one part to the next.\n"
                f"Part before: {before}\nPart after: {after}\n"
                "Give the number of the FIRST line of the part after: the line where it is introduced. The result "
                "of the part before, the reactions to it, thanks and goodbyes still belong to the part before. "
                "Return 0 if the parts are really the same thing.\n\n"
                f"Lines:\n{_numbered(rows, lo_, hi_)}")
        try:
            got = _as_int(_chat(model, SYSTEM, user, AD_SCHEMA_ONE, max_tokens=30).get("line"), -1)
        except BrainError:
            got = li - lo_ + 1
        if got == 0:
            continue
        line = lo_ + got - 1 if 1 <= got <= hi_ - lo_ else li
        if line > bounds[-1] + 2:
            bounds.append(line)
            starts.setdefault(line, starts.get(li, {"about": "", "ad": False}))
    bounds.append(n)

    # 2. read each section on its own
    events: list[dict] = []
    ads: list[tuple[int, int]] = []  # (first line, last line) of each sponsor read
    checked: set[tuple[int, int]] = set()
    prev_what = ""
    k, splits = 0, 0

    def split_at(*lines: int) -> bool:
        nonlocal splits
        new = [x for x in lines if a + 2 < x < b - 1 and x not in bounds]
        if not new or splits > 3 * len(bounds):
            return False
        bounds.extend(new)
        bounds.sort()
        splits += 1
        return True

    while k < len(bounds) - 1:
        a, b = bounds[k], bounds[k + 1]
        k += 1
        in_ad = any(f <= a + 1 and b - 2 <= last for f, last in ads)
        r: dict = {}
        if not in_ad:
            log(f"Local AI: understanding event {k} of {len(bounds) - 1}")
            b_cap = min(b, a + 260)  # very long sections: the model reads the first 260 lines
            evs = [e for e in audio_events if rows[a]["start"] <= e.peak < rows[b - 1]["end"]]
            sounds = "; ".join(f"line {_line_at(rows, a, b, e.peak)}: {e.label}" for e in evs[:12])
            schema = json.loads(json.dumps(EVENT_SCHEMA))
            schema["properties"]["kind"]["enum"] = kinds
            user = (
                f"{about}\nWhat makes a good clip here: {guide}\n" + (f"{taste}\n" if taste else "")
                + ("The user wants HYPE clips: score the loudest, most intense events highest.\n"
                   if hype_first else "")
                + (f"The section before this one: {prev_what}\n" if prev_what else "")
                + "\nRead this section of the video and understand what is going on.\n"
                "- what_happens: one sentence: what this is about and HOW IT ENDS (who won, made it or failed, "
                "the answer, the punchline)\n"
                "- continues_previous: true only if this is the same challenge, game or story as the section "
                "before (for example its result), false if it is something new\n"
                "- ad: true if it is a sponsor read, an ad or a plug\n"
                "- climax_line: the line number where it pays off: the decisive result is revealed (the final "
                "attempt succeeds or fails, someone wins), the punchline lands, the answer is given. If there "
                "are several attempts, the decisive one.\n"
                "- setup_line: the line number where a short clip should start so a stranger understands what "
                "is at stake before the climax: the introduction of the decisive attempt, the question, the bet. "
                "It must come before climax_line, ideally less than 60 seconds before it.\n"
                "- new_thing_line: if something NEW starts inside these lines (a different challenge, game or "
                "topic, a sponsor read or ad, or the video comes back from an ad), the number of the line where "
                "it starts; otherwise 0\n"
                "- title: catchy, max 60 characters, about THIS section, main keyword early; " + HOOK_GUIDE + "\n"
                "- score 0-100: how good this whole section would be as a short (funny, tense, surprising, "
                "emotional, satisfying payoff). Intros, rules explanations, ads and filler score low.\n"
                f"- kind: one of {', '.join(kinds)}\n\n"
                + (f"Sounds detected: {sounds}\n" if sounds else "")
                + f"Lines:\n{_numbered(rows, a, b_cap)}"
            )
            try:
                r = _chat(model, SYSTEM, user, schema, max_tokens=500)
            except BrainError as exc:
                log(f"Local AI skipped event {k}: {exc}")
                continue
            nl = _as_int(r.get("new_thing_line"), 0)
            if nl > 0 and split_at(a + nl - 1):
                # the section holds two things (a missed boundary): split it and read each part again
                k -= 1
                continue
            looks_ad = bool(r.get("ad")) or starts.get(a, {}).get("ad", False) \
                or any(AD_WORDS.search(rows[i]["text"]) for i in range(a, b))
            if looks_ad and (a, b) not in checked:
                checked.add((a, b))
                span = _ad_span(rows, a, b, model)
                if span:
                    f, last_ad = span
                    ads.append(span)
                    if f < a and events and not events[-1]["ad"]:  # the ad began inside the event before
                        prev_ev = events[-1]
                        prev_ev["end"] = min(prev_ev["end"], rows[f]["start"])
                        for key in ("climax", "climax_end", "setup"):
                            prev_ev[key] = min(prev_ev[key], prev_ev["end"])
                    # cut the ad out as its own section; what's left of this section (and the next) is read again
                    new_bounds = sorted(set(bounds[:k] + ([f] if f > a + 2 else [])
                                            + ([last_ad + 1] if last_ad + 1 < n else [])
                                            + [x for x in bounds[k:] if x > last_ad + 1] + [n]))
                    if new_bounds != bounds and splits <= 3 * len(bounds):
                        bounds[:] = new_bounds
                        splits += 1
                        k -= 1
                        continue
                    in_ad = f <= a + 1 and b - 2 <= last_ad
        if in_ad:
            ev = {"start": rows[a]["start"], "end": rows[b]["start"] if b < n else duration,
                  "setup": rows[a]["start"], "climax": rows[a]["start"], "climax_end": rows[a]["end"],
                  "what_happens": "Sponsor read", "kind": kinds[0], "title": "Sponsor read", "hook": "",
                  "score": 0, "ad": True}
            events.append(ev)
            continue
        cl = a + min(max(_as_int(r.get("climax_line"), b - a), 1), b - a) - 1
        sl = min(a + max(_as_int(r.get("setup_line"), 1), 1) - 1, cl)
        ev = {
            "start": rows[a]["start"], "end": rows[b]["start"] if b < n else duration,
            "setup": rows[sl]["start"], "climax": rows[cl]["start"], "climax_end": rows[cl]["end"],
            "what_happens": str(r.get("what_happens", "")), "kind": str(r.get("kind", "")),
            "title": str(r.get("title", ""))[:80], "hook": str(r.get("hook", ""))[:60],
            "score": max(0, min(100, _as_int(r.get("score"), 50))), "ad": False,
        }
        last = events[-1] if events else None
        if last and r.get("continues_previous") and not last["ad"] and ev["end"] - last["start"] <= 600:
            # the same commotion, split in two: one event, its climax from the part that pays off
            main = ev if ev["score"] >= last["score"] - 10 else last
            for key in ("setup", "climax", "climax_end", "title", "hook", "kind"):
                last[key] = main[key]
            last["what_happens"] = (last["what_happens"] + " " + ev["what_happens"]).strip()
            last["score"] = max(last["score"], ev["score"])
            last["end"] = ev["end"]
        else:
            events.append(ev)
        prev_what = ev["what_happens"]
    # a very short event right before another one is usually its introduction ("up next: LeBron"), and a short
    # event whose climax is its very last line pays off in the next one: either way it joins the next event
    out: list[dict] = []
    for e in events:
        prev = out[-1] if out else None
        intro = prev and (prev["end"] - prev["start"] < 20
                          or (prev["end"] - prev["start"] < 60 and prev["climax_end"] >= prev["end"] - 10))
        if prev and intro and not prev["ad"] and not e["ad"] and e["end"] - prev["start"] <= 600:
            e["start"] = prev["start"]
            out[-1] = e
        else:
            out.append(e)
    return [e for e in out if e["end"] - e["start"] >= 4]


def _line_at(rows: list[dict], a: int, b: int, t: float) -> int:
    """Number (from 1) of the line in rows[a:b] that is playing at time t."""
    i = a
    while i + 1 < b and rows[i + 1]["start"] <= t:
        i += 1
    return i - a + 1


BEAT_SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "setup_line": {"type": "integer"},
                    "payoff_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "what_happens": {"type": "string"},
                    "kind": {"type": "string"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "score": {"type": "integer"},
                },
                "required": ["setup_line", "payoff_line", "end_line", "what_happens", "kind", "title", "hook",
                             "score"],
            },
        }
    },
    "required": ["moments"],
}


def beats(segments: list[dict], event: dict, sit: dict, model: str, log: LogFn, lo: float, hi: float,
          taste: str = "", chunk: int = 110, overlap: int = 25) -> list[dict]:
    """Every moment inside one long event that works as a short on its own: each attempt with its result, each
    elimination, a funny exchange, a reveal. Each moment ends after its payoff and the reaction to it.
    Returns moments with start, payoff, payoff_end, end, what_happens, kind, title, hook, score."""
    rows = [{"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()}
            for s in segments if s["text"].strip() and event["start"] <= s["start"] < event["end"]]
    if len(rows) < 4:
        return []
    vtype = sit.get("video_type", "other")
    guide, _ = TYPE_GUIDE.get(vtype, TYPE_GUIDE["other"])
    kinds = TYPE_KINDS.get(vtype, ["funny", "story", "surprise", "emotional", "insight", "reaction"])
    schema = json.loads(json.dumps(BEAT_SCHEMA))
    schema["properties"]["moments"]["items"]["properties"]["kind"]["enum"] = kinds
    found: list[dict] = []
    n = len(rows)
    i0 = 0
    while i0 < n:
        i1 = min(n, i0 + chunk)
        user = (
            f"About this video: {sit.get('summary', '')}\nType: {vtype}.\n"
            f"This part of the video: {event.get('what_happens', '')}\n"
            f"What makes a good clip here: {guide}\n" + (f"{taste}\n" if taste else "")
            + f"\nThis part is too long for one short. List EVERY moment inside it that works as a short on its "
            f"own ({lo:.0f}-{hi:.0f} seconds): each attempt with its result, each elimination or win, each funny "
            "exchange, each reveal or big reaction. Moments must not overlap.\n"
            "For each moment:\n"
            "- setup_line: where it is introduced, so a stranger understands what is at stake\n"
            "- payoff_line: the line where it pays off: the result is known (made it or failed, who is out, who "
            "won), the punchline lands, the answer is given\n"
            "- end_line: the last line of the reaction to the payoff, before the talk moves on. A moment NEVER "
            "ends before its payoff and the reaction to it.\n"
            "- what_happens: one sentence: what happens and how it ends\n"
            "- title: catchy, max 60 characters, about THIS moment, main keyword early; " + HOOK_GUIDE + "\n"
            "- score 0-100: how good it is as a short (funny, tense, surprising, satisfying payoff). Rules "
            "explanations and filler score low.\n"
            f"- kind: one of {', '.join(kinds)}\n\n"
            f"Lines:\n{_numbered(rows, i0, i1)}"
        )
        try:
            res = _chat(model, SYSTEM, user, schema, max_tokens=1600)
        except BrainError as exc:
            log(f"Local AI skipped part of an event: {exc}")
            res = {"moments": []}
        for m in res.get("moments", []):
            s_ = i0 + _as_int(m.get("setup_line"), 0) - 1
            p_ = i0 + _as_int(m.get("payoff_line"), 0) - 1
            e_ = i0 + _as_int(m.get("end_line"), 0) - 1
            if not (i0 <= s_ <= p_ < i1):
                continue
            e_ = min(max(e_, p_), i1 - 1)
            if i1 < n and p_ >= i1 - overlap // 2:  # its reaction may lie past this chunk: the next one sees it
                continue
            found.append({
                "start": rows[s_]["start"], "payoff": rows[p_]["start"], "payoff_end": rows[p_]["end"],
                "end": rows[e_]["end"], "what_happens": str(m.get("what_happens", "")),
                "kind": str(m.get("kind", "")), "title": str(m.get("title", ""))[:80],
                "hook": str(m.get("hook", ""))[:60], "score": max(0, min(100, _as_int(m.get("score"), 50))),
            })
        if i1 >= n:
            break
        i0 = i1 - overlap
    # the same moment seen by two chunks, or moments that overlap: keep the better one
    out: list[dict] = []
    for m in sorted(found, key=lambda m: -m["score"]):
        if all(m["end"] <= o["start"] + 2 or m["start"] >= o["end"] - 2 for o in out):
            out.append(m)
    return sorted(out, key=lambda m: m["start"])


FINISH_SCHEMA = {
    "type": "object",
    "properties": {
        "building_to": {"type": "string"},
        "shown_before_end": {"type": "boolean"},
        "outcome_line": {"type": "integer"},
        "reaction_end_line": {"type": "integer"},
        "new_thing_line": {"type": "integer"},
    },
    "required": ["building_to", "shown_before_end", "outcome_line", "reaction_end_line", "new_thing_line"],
}


def finish(segments: list[dict], start: float, end: float, about: str, model: str,
           look_ahead: float = 45.0) -> float | None:
    """Does the clip [start, end] show how its commotion turns out? If the result (the reveal, who is out, who
    won, the punchline) only comes after the cut, return the time to end at instead (after the reaction to it).
    None: the ending is fine.

    A small model misjudges this both ways, so it is asked twice: once from the lines alone, once also told what
    the clip is about. The clip is extended if either finds the result after the cut: a few seconds too long is
    far better than a clip that stops before its climax. Both are guarded: never past the start of the next
    thing (a new challenge, an ad)."""
    rows = [{"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()}
            for s in segments if s["text"].strip()]
    before = [r for r in rows if start <= r["start"] < end][-25:]
    after = [r for r in rows if end <= r["start"] < end + look_ahead][:30]
    if not before or not after:
        return None
    ends = [_finish_once(before, after, "", model)]
    if about:
        ends.append(_finish_once(before, after, about, model))
    ends = [e for e in ends if e]
    return max(ends) if ends else None


def _finish_once(before: list[dict], after: list[dict], about: str, model: str) -> float | None:
    user = (
        (f"The clip is about: {about}\n" if about else "")
        + "Here are the last lines of a short clip, then the clip ends, then the lines that come AFTER it in the video "
        "(they are NOT in the clip).\n\n"
        "Last lines of the clip:\n" + "\n".join(f"- {r['text']}" for r in before)
        + "\n\n>>> THE CLIP ENDS HERE <<<\n\nLines after the clip:\n"
        + "\n".join(f"{i}: {r['text']}" for i, r in enumerate(after, 1))
        + "\n\nWhat is the clip building up to (the result of the attempt, the reveal, who is eliminated or "
        "wins, the answer, the punchline)? Is that already shown before the clip ends?\n"
        "- building_to: a few words\n"
        "- shown_before_end: true if a viewer already knows how it turns out before the clip ends\n"
        "- outcome_line: if not, the number of the line after the clip where it turns out; 0 if it is shown\n"
        "- reaction_end_line: the last line of the reaction to the outcome, before the talk moves on to "
        "something new; 0 if it is shown\n"
        "- new_thing_line: the number of the first line after the clip where something NEW starts (a new "
        "challenge, game, round, topic, rules for the next game, an ad); 0 if nothing new starts"
    )
    r = _chat(model, SYSTEM, user, FINISH_SCHEMA, max_tokens=150)
    if r.get("shown_before_end", True):
        return None
    o = _as_int(r.get("outcome_line"), 0)
    if not 1 <= o <= len(after):
        return None
    new = _as_int(r.get("new_thing_line"), 0)
    if 1 <= new <= o:  # the "outcome" is in the next thing: the clip's own commotion is already over
        return None
    e = min(max(_as_int(r.get("reaction_end_line"), o), o), len(after), o + 6)
    if 1 <= new <= e:
        e = max(o, new - 1)
    while e > o and after[e - 1]["end"] > after[o - 1]["end"] + 12:  # a reaction, not the next scene
        e -= 1
    return after[e - 1]["end"]


# lines that tell how a commotion turns out, and lines that start the next thing
OUTCOME_WORDS = re.compile(
    r"\b((have|has|are|is|been|now|just) (all )?(now )?(been )?eliminated\b|"
    r"(team|you|he|she|they) (moves?|moving) on|is moving on|the winner (is|of)|(you|he|she|they|we) (won|win)\b|"
    r"got it right|got it wrong|(is|are|you'?re) (out|safe)\b|"
    r"(i|we) (pick|choose|vote for) (you|this|that|him|her|them)\b|my vote (goes|is)\b)", re.IGNORECASE)
NEXT_WORDS = re.compile(r"\b(up next|next (challenge|game|round|up)|for (our|the) next|this next|"
                        r"moving on to|let'?s move on|new challenge)\b", re.IGNORECASE)


def outcome_after(segments: list[dict], start: float, end: float, window: float = 35.0) -> float | None:
    """Without a model: if the clip's last 15 s say nothing about how it turns out, but a line right after the
    cut does ("you've been eliminated", "your team moves on", "the winner is..."), the time to end at instead.
    It never looks past the start of the next thing ("up next...")."""
    rows = [s for s in segments if s["text"].strip()]
    tail = " ".join(s["text"] for s in rows if max(start, end - 15) <= s["start"] < end)
    if OUTCOME_WORDS.search(tail):
        return None
    for s in rows:
        if s["start"] < end - 1.0:
            continue
        if s["start"] > end + window or NEXT_WORDS.search(s["text"]):
            return None
        if OUTCOME_WORDS.search(s["text"]):
            return float(s["end"])
    return None


def sentence_end(segments: list[dict], end: float, limit: float = 25.0) -> float:
    """Never stop mid-sentence: if the line at the cut runs straight on into the next one ("the winner of a
    million dollars for their" | "subscribers is Phil DeFranco"), end after the next one."""
    rows = [s for s in segments if s["text"].strip()]
    for i, s in enumerate(rows):
        if abs(float(s["end"]) - end) > 1.0 or i + 1 >= len(rows):
            continue
        text, nxt = s["text"].strip(), rows[i + 1]
        unfinished = not re.search(r"[.!?…\"')\]]$", text)
        if unfinished and float(nxt["start"]) - float(s["end"]) < 0.6 and float(nxt["end"]) - end <= limit:
            return float(nxt["end"])
        break
    return end


HEADLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "what_happens": {"type": "string"},
        "title": {"type": "string"},
        "hook": {"type": "string"},
        "kind": {"type": "string"},
    },
    "required": ["what_happens", "title", "hook", "kind"],
}


def headline(segments: list[dict], sit: dict, model: str) -> dict:
    """Title, on-screen hook and a one-line summary for a clip the user already cut."""
    vtype = sit.get("video_type", "other")
    kinds = TYPE_KINDS.get(vtype, KINDS)
    schema = json.loads(json.dumps(HEADLINE_SCHEMA))
    schema["properties"]["kind"]["enum"] = kinds
    lines = _lines(segments)[:400]
    user = (
        f"About this clip: {sit.get('summary', '')}\nType: {vtype}. People: "
        f"{', '.join(sit.get('people') or []) or 'unknown'}. Game/topic: {sit.get('game_or_topic') or 'unknown'}.\n\n"
        "This is a finished short clip. Read all of it and write:\n"
        "- what_happens: one sentence for a viewer: what happens and how it ends\n"
        "- title: a YouTube Shorts title, max 60 characters, the game, person or topic first, curiosity without "
        "lying, no hashtags, no emojis\n"
        "- " + HOOK_GUIDE + "\n"
        f"- kind: one of {', '.join(kinds)}\n\n"
        f"Transcript:\n{_fmt(lines)}"
    )
    return _chat(model, SYSTEM, user, schema, max_tokens=300)
