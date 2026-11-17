"""YouTube title, description, hashtags and tags for a clip, written from the clip's own words.

The text reads like a person posted it: no mention of tools, automation or how the clip was made.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

GAMES = {
    "valorant": "Valorant", "minecraft": "Minecraft", "fortnite": "Fortnite", "counter-strike": "CS2", "cs2": "CS2",
    "csgo": "CSGO", "apex": "Apex Legends", "overwatch": "Overwatch", "league of legends": "League of Legends",
    "elden ring": "Elden Ring", "pubg": "PUBG", "playerunknown": "PUBG", "marvel rivals": "Marvel Rivals",
    "sea of thieves": "Sea of Thieves", "warzone": "Warzone", "call of duty": "Call of Duty", "rocket league": "Rocket League",
    "roblox": "Roblox", "gta": "GTA", "red dead": "Red Dead Redemption", "wukong": "Black Myth Wukong",
    "spider-man": "Spider-Man", "the finals": "The Finals", "dead island": "Dead Island 2", "the forest": "The Forest",
    "it takes two": "It Takes Two", "a way out": "A Way Out", "wuthering waves": "Wuthering Waves",
    "last of us": "The Last of Us", "assassin": "Assassin's Creed", "aim lab": "Aim Lab", "rust": "Rust",
    "dota": "Dota 2", "genshin": "Genshin Impact", "fifa": "EA FC", "ea fc": "EA FC", "nba 2k": "NBA 2K",
}
GAME_WORDS = {"spike", "clutch", "ace", "headshot", "respawn", "loadout", "ult", "ranked", "lobby", "frag", "nade",
              "smoke", "flank", "peek", "rotate", "squad", "boss", "level", "quest", "loot", "gg", "kill", "kills"}
STOP = set("""
a about above after again against all am an and any are aren't as at be because been before being below between both
but by can can't cannot could couldn't did didn't do does doesn't doing don't down during each few for from further
get got gonna had hadn't has hasn't have haven't having he he'd he'll he's her here here's hers herself him himself his
how how's i i'd i'll i'm i've if in into is isn't it it's its itself just know let's like me more most mustn't my
myself no nor not now of off oh ok okay on once only or other ought our ours ourselves out over own really right
said same say says see shan't she she'd she'll she's should shouldn't so some such than that that's the their theirs
them themselves then there there's these they they'd they'll they're they've thing things think this those through
to too um uh under until up us very want was wasn't we we'd we'll we're we've well were weren't what what's when
when's where where's which while who who's whom why why's will with won't would wouldn't yeah yes you you'd you'll
you're you've your yours yourself yourselves go going come came make made take one two three also actually
literally gotta wanna kind sort mean back still even never always every much many lot lots way something anything
""".split())
# never put these in tags/hashtags (keeps posts free of tool/automation talk)
BANNED = {"ai", "a.i", "gpt", "chatgpt", "claude", "openai", "generated", "automated", "automatic", "bot", "clipper",
          "algorithm", "llm", "artificial", "intelligence"}
CATEGORY = {"gaming": "20", "entertainment": "24", "people": "22", "education": "27", "news": "25", "comedy": "23"}
BAD_CHARS = re.compile(r"[<>]")


def _clean(text: str) -> str:
    text = BAD_CHARS.sub("", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _clip_words(job_dir: Path, clip: dict) -> list[str]:
    """Words spoken inside the clip, from the job's saved transcript."""
    work = job_dir / "work" / clip["part"] if clip.get("part") else job_dir / "work"
    path = work / "transcript.json"
    if not path.exists():
        return []
    try:
        segs = json.loads(path.read_text(encoding="utf-8")).get("segments", [])
    except (OSError, json.JSONDecodeError):
        return []
    return [w["word"] for s in segs for w in s.get("words", []) if clip["start"] <= w["start"] <= clip["end"]]


def _topic(job: dict, words: list[str]) -> tuple[str | None, bool]:
    """(game name if we can tell, looks like gaming)."""
    sit = job.get("situation") or {}
    if sit.get("video_type") == "gaming":
        topic = (sit.get("game_or_topic") or "").strip()
        known = next((name for key, name in GAMES.items() if key in topic.lower()), None)
        return known or (topic[:30] if topic and topic.lower() not in ("unknown", "gaming") else None), True
    hay =" ".join([job.get("name", ""), (job.get("source_info") or {}).get("title", ""),
                    Path(job.get("source", "")).name]).lower()
    for key, name in GAMES.items():
        if key in hay:
            return name, True
    spoken = {re.sub(r"[^\w-]", "", w.lower()) for w in words}
    for key, name in GAMES.items():
        if " " not in key and key in spoken and key not in ("rust", "apex"):
            return name, True
    return None, len(spoken & GAME_WORDS) >= 2


def _keywords(words: list[str], limit: int = 8) -> list[str]:
    tokens = [re.sub(r"[^\w'-]", "", w.lower()).strip("'-") for w in words]
    tokens = [t for t in tokens if len(t) > 3 and t not in STOP and t not in BANNED and not t.isdigit()]
    return [w for w, _ in Counter(tokens).most_common(limit)]


def _norm_words(text: str) -> set[str]:
    return {w for w in re.sub(r"[^\w' ]", " ", text.lower()).split() if w not in STOP}


def _similar(a: str, b: str) -> bool:
    wa, wb = _norm_words(a), _norm_words(b)
    if not wa or not wb:
        return a.strip().lower() == b.strip().lower()
    return len(wa & wb) / min(len(wa), len(wb)) >= 0.6


def _best_quote(words: list[str], avoid: list[str]) -> str:
    """A complete spoken sentence of 5-18 words that doesn't repeat the title or hook."""
    sentences, cur = [], []
    for w in words:
        cur.append(w)
        if re.search(r"[.!?]['\"”)]*$", w):
            sentences.append(cur)
            cur = []
    best = ""
    for s in sentences:
        text = _clean(" ".join(s))
        text = re.sub(r"^(so|and|but|well|um|uh)\b[, ]*", "", text, flags=re.I)
        n = len(text.split())
        if 5 <= n <= 18 and not any(_similar(text, x) for x in avoid if x) and len(text) > len(best):
            best = text[:1].upper() + text[1:]
    return best


def _hashtag(text: str) -> str:
    words = re.sub(r"[^\w ]", "", text.replace("'", "")).split()
    tag = "".join(w if w.isupper() or not w.islower() else w.capitalize() for w in words)
    return f"#{tag}" if tag and tag.lower() not in BANNED else ""


def build_metadata(job: dict, clip: dict, kind: str, job_dir: Path) -> dict:
    words = _clip_words(job_dir, clip)
    game, gaming = _topic(job, words)
    keywords = _keywords(words)
    # YouTube SEO: titles under 60 characters (mobile cuts the rest) with the main keyword early
    title = _clean(clip.get("title") or "")
    if not title:
        title = _clean(" ".join(words[:12])) or "Wait for it"
    if len(title) > 60:
        title = title[:60].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    if game and game.lower() not in title.lower():
        if len(title) + len(game) + 3 <= 60:
            title = f"{title} | {game}"
        elif len(game) + 2 + 20 <= 60:  # keyword first, shortened title after it
            rest = title[: 60 - len(game) - 2].rsplit(" ", 1)[0].rstrip(" ,;:-")
            title = f"{game}: {rest}"

    hook = _clean(clip.get("hook") or "")
    lines = []
    # first ~160 characters act as the search snippet: say what happens, with the keyword
    summary = _clean(re.sub(r"\s*On screen:.*$", "", clip.get("reason") or ""))
    if summary and clip.get("source") in ("local-ai", "claude") and not summary.lower().startswith("stands out for"):
        if game and game.lower() not in summary.lower():
            summary = f"{game}: {summary}"
        lines.append(summary[:160])
    if hook and not _similar(hook, title) and not any(_similar(hook, x) for x in lines):
        lines.append(hook)
    quote = _best_quote(words, avoid=[title, hook])
    if quote:
        lines.append(f"“{quote}”")
    src = job.get("source_info") or {}
    if src.get("url") and src.get("title"):
        by = f" by {src['uploader']}" if src.get("uploader") else ""
        lines.append(f"From “{_clean(src['title'])}”{by}\n{src['url']}")
    if game:
        lines.append(f"More {game} clips on the channel.")

    tags_h: list[str] = []
    if kind == "short":
        tags_h.append("#shorts")
    if game:
        tags_h.append(_hashtag(game))
    if gaming:
        tags_h.append("#gaming")
    cat = (clip.get("category") or "").lower()
    tags_h.append({"funny": "#funny", "joke": "#funny", "banter": "#funny", "crowd reaction": "#funny",
                   "hype": "#hype", "clutch": "#clutch", "fail": "#fail", "story": "#storytime",
                   "reaction": "#reaction", "goal": "#goal", "big_play": "#highlights", "celebration": "#hype",
                   "argument": "#debate", "opinion": "#debate", "surprise": "#plottwist", "reveal": "#plottwist",
                   "emotional": "#emotional", "insight": "#mindset",
                   "action": "#clutch" if gaming else "#viral"}.get(cat, "#viral"))
    if src.get("uploader") and len(src["uploader"]) <= 20:
        tags_h.append(_hashtag(src["uploader"]))
    counts = Counter(re.sub(r"[^\w'-]", "", w.lower()) for w in words)
    for k in keywords[:4]:  # only words that clearly carry the clip
        if counts[k] >= 3 and len(k) >= 5:
            tags_h.append(_hashtag(k))
    hashtags = []
    for h in tags_h:
        if h and h.lower() not in (x.lower() for x in hashtags):
            hashtags.append(h)
    hashtags = hashtags[:6]

    description = "\n\n".join(lines + [" ".join(hashtags)])
    description = BAD_CHARS.sub("", description)[:4800]

    tags = []
    for t in ([game] if game else []) + (["gaming", "gameplay", "highlights"] if gaming else ["highlights", "clips"]) \
            + keywords + ([src["uploader"]] if src.get("uploader") else []):
        t = _clean(t)
        if t and t.lower() not in BANNED and t.lower() not in (x.lower() for x in tags) and len(t) <= 30:
            tags.append(t)
    while tags and len(",".join(tags)) > 480:
        tags.pop()

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "category_id": CATEGORY["gaming"] if (game or gaming) else CATEGORY["entertainment"],
        "privacy": "public",
    }
