"""End-to-end job: long video in, ranked highlight clips (landscape + 9:16 shorts) out."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

import numpy as np

from . import hype, media, music
from .analyze import Timeline, dead_air, find_highlights, punch_ins
from .options import Options
from .output import clips_directory
from .render import render_clip
from .transcribe import transcribe
from .paths import data_root

ROOT = data_root()
MUSIC_DIR = ROOT / "music"

# stage -> (start %, end %)
STAGES = {
    "probe": (0, 2),
    "audio": (2, 6),
    "transcribe": (6, 55),
    "analyze": (55, 68),
    "render": (68, 100),
}

UpdateFn = Callable[..., None]


def run_pipeline(src: Path, job_dir: Path, opts: Options, update: UpdateFn, window: dict | None = None) -> dict:
    """update(stage=..., progress=0..100, message=..., **extra) is called as work advances.

    window (live recording parts): {"label": "p003", "offset": seconds into the recording where this
    file starts, "new_from": seconds into this file where unseen material begins, "exclude": [(start,
    end), ...] ranges in this file already used by earlier clips}. Clips must reach into the new
    material and avoid excluded ranges; an empty result is fine (nothing worth clipping yet).
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    work = job_dir / "work" / window["label"] if window else job_dir / "work"
    out = clips_directory(job_dir, opts.output_dir)
    work.mkdir(parents=True, exist_ok=True)

    def step(stage: str, frac: float, msg: str, **extra):
        a, b = STAGES[stage]
        update(stage=stage, progress=round(a + (b - a) * max(0.0, min(frac, 1.0)), 1), message=msg, **extra)

    def log(msg: str):
        update(log=msg)

    t0 = time.time()
    step("probe", 0, "Reading video")
    info = media.probe(src)
    if info.duration <= 0:
        raise media.MediaError("Could not read video duration.")
    w, h = info.display_size
    step("probe", 1, f"{info.duration / 60:.1f} min, {w}x{h}, {info.fps:.0f} fps",
         video={"duration": info.duration, "width": w, "height": h, "fps": info.fps})

    # ---------------------------------------------------------------- audio
    if info.has_audio:
        step("audio", 0, "Extracting audio")
        if info.audio_tracks > 1:
            log(f"Found {info.audio_tracks} audio tracks (for example game sound and mic); mixing them all.")
        wav = media.extract_audio(src, work / "audio16k.wav", info.audio_tracks)
        loud_db, hop = media.audio_rms(wav)
        step("audio", 0.6, "Listening for hype: shouting, crowd reactions, action bursts")
        audio_feats = hype.audio_features(wav, hop)
    else:
        wav, hop, audio_feats = None, 0.5, None
        loud_db = np.full(int(info.duration / hop) + 1, -60.0)
        log("Video has no audio track: captions and speech analysis are skipped.")
    step("audio", 1, "Audio ready")
    if audio_feats is not None:  # kept so a clip can be re-edited later without re-reading the audio
        np.savez_compressed(work / "signals.npz", loud_db=loud_db, hop=np.array(hop), **audio_feats)

    # ---------------------------------------------------------------- transcribe
    if wav is not None:
        transcript = transcribe(
            wav, work / "transcript.json", info.duration, opts.whisper_model, opts.language,
            progress=lambda f, m: step("transcribe", f, m),
        )
        log(f"Transcribed with Whisper {transcript.get('model')} on {transcript.get('device')}, "
            f"language '{transcript.get('language')}', {len(transcript['segments'])} segments")
    else:
        transcript = {"language": "", "segments": []}
    step("transcribe", 1, "Transcript ready")

    # ---------------------------------------------------------------- analyze
    step("analyze", 0.05, "Measuring visual activity")
    try:
        scene = media.scene_activity(src, info.duration, hop)
    except media.MediaError as exc:  # optional signal; keep going without it
        log(str(exc))
        scene = np.zeros(int(info.duration / hop) + 1)
    step("analyze", 0.4, "Scoring moments")
    tl = Timeline(transcript, loud_db, scene, hop, info.duration, audio=audio_feats)
    if tl.events:
        kinds: dict[str, int] = {}
        for e in tl.events:
            kinds[e.label] = kinds.get(e.label, 0) + 1
        log(f"Found {len(tl.events)} hype moments: " + ", ".join(f"{v} {k}" for k, v in kinds.items()))
    allow = None
    if window:
        new_from = float(window.get("new_from", 0.0))
        exclude = [tuple(r) for r in window.get("exclude", [])]

        def allow(a: float, b: float) -> bool:
            # must contain at least 5 s of new material, and not overlap clips made from earlier parts
            return b >= new_from + 5 and all(b <= x0 or a >= x1 for x0, x1 in exclude)

    title = src.stem
    try:  # the job's name is the video title for links and live streams
        title = json.loads((job_dir / "job.json").read_text(encoding="utf-8")).get("name") or title
    except (OSError, ValueError):
        pass
    if opts.ready_clip:  # a finished clip: keep the whole thing, write its title and hook
        from .analyze import ready_clip
        clips = [ready_clip(tl, opts, log, title)]
    else:
        clips = find_highlights(tl, opts, log, allow, title=title, video=str(src))
    if tl.situation:
        update(situation=tl.situation)
    if getattr(tl, "event_map", None):  # what the AI understood, for checking its picks
        (work / "events.json").write_text(json.dumps({"situation": tl.situation, "events": tl.event_map},
                                                     indent=1, ensure_ascii=False), encoding="utf-8")
    if window:
        for c in clips:
            c.offset, c.part = float(window["offset"]), window["label"]
    if not clips:
        if window:
            step("render", 1, "No strong moments in this part", clips=[])
            return {"clips": [], "elapsed": round(time.time() - t0, 1)}
        raise RuntimeError("No highlight candidates found (video may be shorter than the minimum clip length).")

    # compact hype curve for the timeline view
    bins = 600
    z = np.clip(tl.hype, 0, None)
    idx = np.linspace(0, len(z), bins + 1).astype(int)
    energy = [float(np.max(z[idx[i]:max(idx[i] + 1, idx[i + 1])])) for i in range(bins)]
    lo, hi = min(energy), max(energy)
    energy = [round((e - lo) / (hi - lo + 1e-9), 3) for e in energy]
    clip_dicts = [c.to_dict() for c in clips]
    picked = "Ready to caption" if opts.ready_clip else f"Picked {len(clips)} clip{'' if len(clips) == 1 else 's'}"
    step("analyze", 1, picked, clips=clip_dicts, energy=energy)

    # ---------------------------------------------------------------- render
    words = [w for s in transcript["segments"] for w in s.get("words", [])]
    music_path = None
    if opts.music:
        music_path = music.pick_track(MUSIC_DIR, opts.music_path)
        log(f"Background music: {music_path.name}")
    kinds = [k for k, on in (("short", opts.make_shorts), ("landscape", opts.make_landscape)) if on]
    if opts.ready_clip and h > w:  # an already-vertical clip: its short is the clip, a "landscape" copy is pointless
        kinds = ["short"]
    total = max(1, len(clips) * len(kinds))
    done = 0
    out.mkdir(parents=True, exist_ok=True)
    prefix = f"{window['label']}_" if window else ""
    for i, clip in enumerate(clips, 1):
        clip_words = [wd for wd in words if clip.start - 1 <= wd["start"] <= clip.end + 1]
        media.thumbnail(src, clip.start + min(2.0, clip.duration / 3), out / f"{prefix}clip{i:02d}.jpg")
        clip.files["thumb"] = f"{prefix}clip{i:02d}.jpg"
        # pacing: shrink dead air (no words, nothing audible), and plan punch-in zooms for shorts
        keep = dead_air(tl, clip.start, clip.end) if opts.tighten_pauses and tl.all_words else None
        keep_ranges = keep or [(0.0, clip.duration)]
        zooms = punch_ins(tl, clip.start, clip.end, keep_ranges) if opts.punch_in else []
        cut = clip.duration - sum(y - x for x, y in keep_ranges)
        if cut >= 0.5:
            log(f"Clip {i}: removed {cut:.1f} s of dead air")
        for kind in kinds:
            step("render", done / total, f"Rendering clip {i}/{len(clips)} ({kind})")
            meta = render_clip(info, clip, clip_words, opts, out, i, kind, music_path, keep=keep,
                               zooms=zooms if kind == "short" else None)
            clip.files[kind] = meta["file"]
            clip.files[f"{kind}_framing"] = meta["framing"]
            clip.files["srt"] = meta["srt"]
            done += 1
            step("render", done / total, f"Rendered clip {i}/{len(clips)} ({kind})",
                 clips=[c.to_dict() for c in clips])
        (out / f"{prefix}clip{i:02d}.json").write_text(json.dumps(clip.to_dict(), indent=2, ensure_ascii=False),
                                               encoding="utf-8")

    result = {"clips": [c.to_dict() for c in clips], "elapsed": round(time.time() - t0, 1)}
    step("render", 1, f"Done: {len(clips)} clip{'' if len(clips) == 1 else 's'} in {result['elapsed'] / 60:.1f} min", **result)
    return result


# --------------------------------------------------------------------------- re-edit one clip by hand

def _edit_timeline(job_dir: Path, info, transcript: dict):
    """The audio signals the clip editor needs (dead air, punch-ins): saved ones, else measured again from
    the job's audio copy. None if neither exists (the clip is then cut exactly, without those)."""
    from .analyze import Timeline

    work = job_dir / "work"
    sig = work / "signals.npz"
    if sig.exists():
        d = np.load(sig)
        hop = float(d["hop"])
        feats = {k: d[k] for k in ("shout", "crowd", "impacts") if k in d}
        loud_db = d["loud_db"]
    else:
        wav = work / "audio16k.wav"
        if not wav.exists():
            return None
        loud_db, hop = media.audio_rms(wav)
        feats = hype.audio_features(wav, hop)
        np.savez_compressed(sig, loud_db=loud_db, hop=np.array(hop), **feats)
    return Timeline(transcript, loud_db, np.zeros(int(info.duration / hop) + 1), hop, info.duration, audio=feats)


def rerender_clip(src: Path, job_dir: Path, opts: Options, clip_dict: dict, index: int, rev: int,
                  log: Callable[[str], None]) -> dict:
    """Render one clip again from the original video with the range, title and hook the user set (index is
    0-based). Same edit as before: captions, framing, music, pauses and zooms. Files get a new name (_e<rev>)
    so the page shows the new version, and the old files are removed. Returns the updated clip."""
    from .analyze import Clip

    info = media.probe(src)
    tpath = job_dir / "work" / "transcript.json"
    transcript = json.loads(tpath.read_text(encoding="utf-8")) if tpath.exists() else {"segments": []}
    words = [w for s in transcript["segments"] for w in s.get("words", [])]
    fields = {k: clip_dict.get(k) for k in ("title", "hook", "reason", "category", "score", "heuristic")}
    clip = Clip(start=float(clip_dict["start"]), end=float(clip_dict["end"]), source="edited",
                **{k: v for k, v in fields.items() if v is not None})
    tl = _edit_timeline(job_dir, info, transcript) if (opts.tighten_pauses or opts.punch_in) else None
    keep = dead_air(tl, clip.start, clip.end) if tl and opts.tighten_pauses and tl.all_words else None
    zooms = punch_ins(tl, clip.start, clip.end, keep or [(0.0, clip.duration)]) if tl and opts.punch_in else []
    music_path = music.pick_track(MUSIC_DIR, opts.music_path) if opts.music else None
    out = clips_directory(job_dir, opts.output_dir)
    old = dict(clip_dict.get("files") or {})
    kinds = [k for k in ("short", "landscape") if old.get(k)] or ["short"]
    n = index + 1
    clip_words = [w for w in words if clip.start - 1 <= w["start"] <= clip.end + 1]
    files: dict = {}
    thumb = f"clip{n:02d}_e{rev}.jpg"
    media.thumbnail(src, clip.start + min(2.0, clip.duration / 3), out / thumb)
    files["thumb"] = thumb
    for kind in kinds:
        log(f"Re-editing clip {n} ({kind})")
        meta = render_clip(info, clip, clip_words, opts, out, n, kind, music_path, keep=keep,
                           zooms=zooms if kind == "short" else None)
        final = f"clip{n:02d}_{kind}_e{rev}.mp4"
        (out / final).unlink(missing_ok=True)
        (out / meta["file"]).replace(out / final)
        files[kind] = final
        files[f"{kind}_framing"] = meta["framing"]
        files["srt"] = meta["srt"]
    for key in ("short", "landscape", "thumb"):  # the previous version's files
        name = old.get(key)
        if name and name not in files.values():
            (out / name).unlink(missing_ok=True)
    d = clip.to_dict()
    d.update(files=files, edited=True, rev=rev)
    (out / f"clip{n:02d}.json").write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    return d
