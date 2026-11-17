from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import math


@dataclass
class Options:
    # Highlight selection
    num_clips: int = 0             # 0 = automatic: every moment good enough to post
    ready_clip: bool = False       # the video already is a finished clip: caption and title it, no cutting
    live_manual: bool = False      # live link: record it and let the user cut clips by hand while watching
    live_chat_clips: bool = False  # with live_manual: also clip the moments the stream's chat explodes, on its own
    chat_only: bool = False        # the chat hype clipper as its own job: record + clip chat explosions, no manual cutting
    min_len: float = 15.0          # seconds
    max_len: float = 60.0          # seconds
    hype_mode: str = "balanced"    # balanced = best moments overall (hype counts a lot) | hype = loud, intense moments
    use_llm: bool = True           # use Claude when ANTHROPIC_API_KEY (or ant profile) is available
    use_brain: bool = True         # understand the video with a local Ollama model when it's running (free)
    brain_model: str = "auto"      # auto keeps 7B/14B selection; qwen3.8:27b is explicit opt-in
    use_vision: bool = True        # let a local vision model look at frames of each candidate moment
    use_review: bool = True        # second, stricter read of each clip: fix start/end, drop weak ones
    llm_model: str = "claude-opus-5-5"

    # Transcription
    whisper_model: str = "auto"    # auto | tiny | base | small | medium | large-v3 | large-v3-turbo
    language: str = ""             # "" = auto-detect

    # Outputs
    output_dir: str = ""           # custom clip base folder; empty keeps jobs/<id>/clips
    make_shorts: bool = True       # 9:16 1080x1920
    make_landscape: bool = True    # original aspect, captions + music
    shorts_framing: str = "auto"   # auto | face | zoom | blur | center

    # Captions
    captions: bool = True
    caption_font: str = "Arial Black"
    caption_color: str = "#FFFFFF"
    highlight_color: str = "#FFE600"
    caption_words: int = 3         # max words on screen at once
    caption_uppercase: bool = True
    caption_reveal: bool = True    # words appear one by one as they're spoken
    tighten_pauses: bool = True    # cut dead air (silent gaps with no words) inside clips
    punch_in: bool = True          # shorts: hard punch-in zooms every few seconds and on hype peaks
    title_overlay: bool = True     # hook title at the top of shorts for first seconds

    # Music
    music: bool = True
    music_path: str = ""           # explicit track; empty = random from music/ folder (or generated)
    music_volume: float = 0.18     # 0..1 relative to speech
    duck: bool = True              # lower music while people talk

    # Audio
    normalize_audio: bool = True

    # Live streams
    live_part_minutes: float = 10.0   # record and clip in parts this long
    live_clips_per_part: int = 2
    live_max_hours: float = 4.0       # stop recording after this long

    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "Options":
        known = {f.name: f for f in fields(cls)}
        kwargs = {}
        for key, value in (data or {}).items():
            if key not in known or value is None or value == "":
                continue
            default = getattr(cls(), key)
            try:
                if isinstance(default, bool):
                    value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "on", "yes")
                elif isinstance(default, int):
                    value = int(float(value))
                elif isinstance(default, float):
                    value = float(value)
                elif isinstance(default, str):
                    value = str(value)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(value):
                    continue
            except (TypeError, ValueError, OverflowError):
                continue
            kwargs[key] = value
        opts = cls(**kwargs)
        opts.num_clips = max(0, min(opts.num_clips, 50))
        opts.min_len = max(5.0, min(opts.min_len, 170.0))
        opts.max_len = max(opts.min_len + 5.0, min(opts.max_len, 180.0))
        opts.caption_words = max(1, min(opts.caption_words, 8))
        opts.music_volume = max(0.0, min(opts.music_volume, 1.0))
        opts.live_part_minutes = max(2.0, min(opts.live_part_minutes, 60.0))
        opts.live_clips_per_part = max(1, min(opts.live_clips_per_part, 10))
        opts.live_max_hours = max(0.5, min(opts.live_max_hours, 24.0))
        return opts

    def to_dict(self) -> dict:
        return asdict(self)
