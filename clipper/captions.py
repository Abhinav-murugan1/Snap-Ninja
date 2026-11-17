"""Word-by-word animated captions as an ASS subtitle file (burned in by ffmpeg/libass)."""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

from .options import Options


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


SOFT = 0.8  # slight edge blur on the outline: smoother, less jagged letters after compression
FILLER = re.compile(r"^(um+|uh+|erm+|hmm+|mm+|ah+)[,.]?$", re.I)


def _clean(text: str) -> str:
    return re.sub(r"[{}\\]", "", text).strip()


def _chunk_words(words: list[dict], max_words: int, max_chars: int) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            gap = w["start"] - cur[-1]["end"]
            text_len = sum(len(x["word"]) + 1 for x in cur) + len(w["word"])
            ends_sentence = cur[-1].get("brk") == "sentence"
            soft_break = cur[-1].get("brk") == "clause" and len(cur) >= 2
            if len(cur) >= max_words or gap > 0.7 or text_len > max_chars or ends_sentence or soft_break:
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[dict], clip_start: float, clip_end: float, width: int, height: int,
              opts: Options, vertical: bool, title: str = "") -> str:
    """words: absolute-time words from the transcript. Returns ASS file text."""
    dur = clip_end - clip_start
    rel = []
    for w in words:
        if w["end"] <= clip_start or w["start"] >= clip_end:
            continue
        raw = _clean(w["word"])
        if not raw or FILLER.match(raw):
            continue
        # remember where sentences/clauses break, then show the word without trailing , . ; :
        brk = "sentence" if re.search(r"[.!?…]['\"”)]*$", raw) else "clause" if re.search(r"[,;:—–-]$", raw) else ""
        word = re.sub(r"(?<![.])[,.;:—–]+$", "", raw).strip() or raw
        rel.append({"start": max(0.0, w["start"] - clip_start), "end": min(dur, w["end"] - clip_start),
                    "word": word.upper() if opts.caption_uppercase else word, "brk": brk})

    if vertical:
        # 9:16 safe area: captions in the band ~66% down, inside the centre 900px; clear of the bottom UI band
        play_w, play_h, font_size, margin_v, outline = 1080, 1920, 86, 650, 7
    else:
        play_w, play_h = width, height
        font_size = max(28, round(62 * height / 1080))
        margin_v = max(30, round(80 * height / 1080))
        outline = max(3, round(5 * height / 1080))
    max_chars = 18 if vertical else 32

    base = _ass_color(opts.caption_color)
    hi = _ass_color(opts.highlight_color)
    black = _ass_color("#000000")
    shadow_col = _ass_color("#000000", 0x60)
    font = _clean(opts.caption_font) or "Arial Black"

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {play_w}",
        f"PlayResY: {play_h}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{font},{font_size},{base},{hi},{black},{shadow_col},-1,0,0,0,100,100,1,0,1,{outline},3,2,"
        f"{110 if vertical else 60},{130 if vertical else 60},{margin_v},1",
        f"Style: Title,{font},{60 if vertical else max(26, round(46 * height / 1080))},{black},{black},"
        f"{_ass_color('#FFFFFF')},{_ass_color('#FFFFFF')},-1,0,0,0,100,100,0,0,3,18,0,8,"
        f"{'110,130' if vertical else '80,80'},{280 if vertical else max(24, round(50 * height / 1080))},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    if title and opts.title_overlay:
        wrapped = "\\N".join(textwrap.wrap(_clean(title), 22 if vertical else 44)[:3])
        # the hook is on screen from frame 1 (no fade-up): a quick pop, then it fades out
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(min(4.0, dur))},Title,,0,0,0,,"
                     f"{{\\fad(0,350)\\fscx88\\fscy88\\t(0,120,\\fscx100\\fscy100)}}{wrapped}")

    if opts.captions and rel:
        chunks = _chunk_words(rel, opts.caption_words, max_chars)
        for ci, chunk in enumerate(chunks):
            c_start = chunk[0]["start"]
            next_start = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else dur
            c_end = chunk[-1]["end"]
            if next_start - c_end < 0.35:  # avoid flicker between close chunks
                c_end = next_start
            c_end = min(max(c_end, c_start + 0.25), dur)
            for wi, w in enumerate(chunk):
                seg_start = c_start if wi == 0 else w["start"]
                seg_end = chunk[wi + 1]["start"] if wi + 1 < len(chunk) else c_end
                if seg_end - seg_start < 0.02:
                    continue
                parts = []
                for wj, other in enumerate(chunk):
                    if wj == wi:
                        # the word being spoken pops in (with a little overshoot) in the highlight colour
                        parts.append(f"{{\\c{hi}\\fscx72\\fscy72\\t(0,90,\\fscx116\\fscy116)"
                                     f"\\t(90,170,\\fscx108\\fscy108)}}{other['word']}{{\\r\\blur{SOFT}}}")
                    elif wj > wi and opts.caption_reveal:
                        # words not spoken yet keep their place but stay invisible, so nothing shifts
                        parts.append(f"{{\\alpha&HFF&}}{other['word']}{{\\r\\blur{SOFT}}}")
                    else:
                        parts.append(other["word"])
                lines.append(f"Dialogue: 0,{_ts(seg_start)},{_ts(seg_end)},Cap,,0,0,0,,"
                             f"{{\\blur{SOFT}}}{' '.join(parts)}")
    return "\n".join(lines) + "\n"


def build_srt(words: list[dict], clip_start: float, clip_end: float, max_words: int = 7) -> str:
    """Plain sidecar subtitles (phrases of up to 7 words) for accessibility and search."""
    dur = clip_end - clip_start
    rel = []
    for w in words:
        if w["end"] <= clip_start or w["start"] >= clip_end:
            continue
        word = _clean(w["word"])
        if word and not FILLER.match(word):
            rel.append({"start": max(0.0, w["start"] - clip_start), "end": min(dur, w["end"] - clip_start),
                        "word": word, "brk": "sentence" if re.search(r"[.!?…]$", word) else ""})
    out = []
    for n, chunk in enumerate(_chunk_words(rel, max_words, 42), 1):
        a, b = chunk[0]["start"], max(chunk[-1]["end"], chunk[0]["start"] + 0.4)

        def srt_ts(t: float) -> str:
            ms = int(round(t * 1000))
            return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

        out.append(f"{n}\n{srt_ts(a)} --> {srt_ts(b)}\n{' '.join(w['word'] for w in chunk)}\n")
    return "\n".join(out)


def write_ass(path: Path, **kwargs) -> Path:
    path.write_text(build_ass(**kwargs), encoding="utf-8-sig")
    return path
