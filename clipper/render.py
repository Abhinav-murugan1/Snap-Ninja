"""Render one clip: cut (tightening dead air), frame, punch in, caption, add music, normalize loudness, encode."""
from __future__ import annotations

import random
from pathlib import Path

from . import framing
from .analyze import Clip, remap
from .captions import build_srt, write_ass
from .media import FFMPEG, FFPROBE, VideoInfo, audio_mix, run, video_encoder
from .options import Options


def audio_duration(path: Path) -> float:
    proc = run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
               check=False)
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def _select_expr(keep: list[tuple[float, float]]) -> str:
    return "+".join(f"between(t\\,{x:.3f}\\,{y:.3f})" for x, y in keep)


def _audio_graph(tracks: int, music: bool, opts: Options, dur: float, sel: str = "") -> str:
    has_speech = tracks > 0
    parts = []
    if has_speech:
        parts.append(audio_mix(0, tracks, "src"))
        speech_in = "[src]"
        if sel:  # drop the dead-air gaps from the speech too
            parts.append(f"[src]aselect='{sel}',asetpts=N/SR/TB[srcs]")
            speech_in = "[srcs]"
    else:
        speech_in = "[2:a]"
    parts.append(f"{speech_in}aresample=48000,aformat=channel_layouts=stereo[sp]")
    if music:
        fade_out = max(0.0, dur - 2.0)
        parts.append(
            f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={opts.music_volume:.3f},"
            f"afade=t=in:st=0:d=1.2,afade=t=out:st={fade_out:.2f}:d=2[mu]"
        )
        if opts.duck and has_speech:
            parts.append("[sp]asplit=2[sp1][sp2]")
            parts.append("[mu][sp2]sidechaincompress=threshold=0.015:ratio=8:attack=20:release=450:makeup=1[mud]")
            parts.append("[sp1][mud]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")
        else:
            parts.append("[sp][mu]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")
        last = "[mix]"
    else:
        last = "[sp]"
    tail = f"afade=t=out:st={max(0.0, dur - 0.25):.2f}:d=0.25"
    if opts.normalize_audio:
        tail = "loudnorm=I=-14:TP=-2.0:LRA=11," + tail
    parts.append(f"{last}{tail}[aout]")
    return ";".join(parts)


def _zoom_expr(zooms: list[tuple[float, float, float]]) -> str:
    expr = "1"
    for t0, t1, z in reversed(zooms):
        expr = f"if(between(in_time\\,{t0:.2f}\\,{t1:.2f})\\,{z:.3f}\\,{expr})"
    return expr


def render_clip(info: VideoInfo, clip: Clip, words: list[dict], opts: Options, out_dir: Path, index: int,
                kind: str, music_path: Path | None, keep: list[tuple[float, float]] | None = None,
                zooms: list[tuple[float, float, float]] | None = None) -> dict:
    """kind: 'short' (1080x1920) or 'landscape' (source aspect).
    keep: clip-relative ranges to keep (dead air removed); zooms: (start, end, zoom) punch-ins on the kept timeline.
    Returns metadata for the output."""
    out_dir.mkdir(parents=True, exist_ok=True)
    src_dur = clip.duration
    keep = keep if keep and len(keep) > 1 else None
    dur = sum(y - x for x, y in keep) if keep else src_dur
    width, height = info.display_size
    vertical = kind == "short"
    stem = f"{clip.part + '_' if clip.part else ''}clip{index:02d}_{kind}"
    ass_name = f"{stem}.ass"

    if vertical:
        speech = [(w["start"] - clip.start, w["end"] - clip.start) for w in words
                  if clip.start <= w["start"] <= clip.end]
        fr = framing.plan(info.path, clip.start, clip.end, width, height, opts.shorts_framing, speech=speech)
        vgraph = fr.video_filter
        out_w, out_h = framing.OUT_W, framing.OUT_H
        frame_mode = fr.mode
    else:
        out_w = min(1920, width) // 2 * 2
        out_h = int(round(height * out_w / width)) // 2 * 2
        vgraph = f"[0:v]scale={out_w}:{out_h}:flags=lanczos,setsar=1[vf]"
        frame_mode = "original"

    # framing works on the original timeline; dead air is removed after it, punch-ins after that
    label = "vf"
    sel = _select_expr(keep) if keep else ""
    if sel:
        vgraph += f";[{label}]select='{sel}',setpts=N/FRAME_RATE/TB[vsel]"
        label = "vsel"
    if vertical and zooms:
        # zoom toward face height (40% down), not the middle, so heads don't get cropped
        vgraph += (f";[{label}]zoompan=z='{_zoom_expr(zooms)}':x='iw/2-(iw/zoom/2)':y='ih*0.4-(ih/zoom*0.4)'"
                   f":d=1:s={out_w}x{out_h}:fps={info.fps:.3f},setsar=1[vzoom]")
        label = "vzoom"

    # captions follow the tightened timeline
    if keep:
        shifted = []
        for w in words:
            a, b = remap(w["start"] - clip.start, keep), remap(w["end"] - clip.start, keep)
            if a is not None:
                shifted.append({**w, "start": clip.start + a, "end": clip.start + (b if b is not None else a + 0.3)})
        cap_words, cap_end = shifted, clip.start + dur
    else:
        cap_words, cap_end = words, clip.end
    write_ass(out_dir / ass_name, words=cap_words, clip_start=clip.start, clip_end=cap_end, width=out_w,
              height=out_h, opts=opts, vertical=vertical, title=(clip.hook or clip.title) if vertical else "")
    srt_name = f"{clip.part + '_' if clip.part else ''}clip{index:02d}.srt"
    (out_dir / srt_name).write_text(build_srt(cap_words, clip.start, cap_end), encoding="utf-8")
    vgraph += f";[{label}]ass={ass_name}[vout]"

    use_music = bool(opts.music and music_path)
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{clip.start:.3f}", "-t", f"{src_dur:.3f}", "-i", info.path]
    if use_music:
        mdur = audio_duration(music_path)
        offset = random.Random(index).uniform(0, max(0.0, mdur - dur - 1)) if mdur > dur + 5 else 0.0
        cmd += ["-stream_loop", "-1", "-ss", f"{offset:.2f}", "-i", str(music_path)]
    else:
        cmd += ["-f", "lavfi", "-t", "1", "-i", "anullsrc=r=48000:cl=stereo"]  # placeholder input #1
    if not info.has_audio:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]  # input #2
    agraph = _audio_graph(info.audio_tracks, use_music, opts, dur, sel if info.has_audio else "")

    out_file = f"{stem}.mp4"
    cmd += ["-filter_complex", vgraph + ";" + agraph, "-map", "[vout]", "-map", "[aout]",
            *video_encoder(), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{dur:.3f}",
            "-movflags", "+faststart", out_file]
    run(cmd, cwd=out_dir)
    return {"file": out_file, "kind": kind, "width": out_w, "height": out_h, "framing": frame_mode,
            "srt": srt_name, "tightened": round(src_dur - dur, 2), "punch_ins": len(zooms or [])}
