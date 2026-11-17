"""Command-line use:  .venv\\Scripts\\python cli.py "long video.mp4" --min 15 --max 60"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from clipper.env import load_env

load_env()

import re  # noqa: E402

from clipper.download import download, is_url  # noqa: E402
from clipper.options import Options  # noqa: E402
from clipper.pipeline import run_pipeline  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Cut a long video into captioned highlight clips and shorts.")
    ap.add_argument("video", help="video file, or a link (YouTube, Kick, Twitch VOD, ...)")
    ap.add_argument("--out", type=Path, default=None, help="output folder (default: jobs/cli-<name>)")
    ap.add_argument("--clips", type=int, default=0, help="at most this many clips (default 0: every good moment)")
    ap.add_argument("--min", dest="min_len", type=float, default=15)
    ap.add_argument("--max", dest="max_len", type=float, default=60)
    ap.add_argument("--no-llm", action="store_true", help="skip Claude, use the offline scorer only")
    ap.add_argument("--no-brain", action="store_true", help="don't use the local AI (Ollama) to understand the video")
    ap.add_argument("--fast", action="store_true", help="use the fast 7B local model")
    ap.add_argument("--smart", action="store_true", help="use the smart 14B local model (slow on GPUs under 12 GB)")
    ap.add_argument("--no-vision", action="store_true", help="don't let the local AI look at video frames")
    ap.add_argument("--no-review", action="store_true", help="skip the editor review of each clip")
    ap.add_argument("--no-tighten", action="store_true", help="keep pauses (don't cut dead air)")
    ap.add_argument("--no-punch", action="store_true", help="no punch-in zooms on shorts")
    ap.add_argument("--hype", action="store_true", help="look for hype and loud moments first "
                    "(shouting, crowd reactions, action bursts, loud spikes)")
    ap.add_argument("--whisper", default="auto", help="tiny|base|small|medium|large-v3|large-v3-turbo|auto")
    ap.add_argument("--language", default="")
    ap.add_argument("--music", default="", help="music file (default: random track from music/)")
    ap.add_argument("--no-music", action="store_true")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--framing", default="auto", choices=["auto", "face", "zoom", "blur", "center"])
    ap.add_argument("--only", choices=["shorts", "landscape"], default=None)
    ap.add_argument("--ready", action="store_true", help="the video is already a finished clip: keep all of it, "
                    "add captions, a title and a hook (no music, pause trimming or zooms unless you ask)")
    args = ap.parse_args()

    opts = Options.from_dict({
        "num_clips": args.clips, "min_len": args.min_len, "max_len": args.max_len,
        "use_llm": not args.no_llm, "whisper_model": args.whisper, "language": args.language,
        "hype_mode": "hype" if args.hype else "balanced", "use_brain": not args.no_brain,
        "brain_model": "qwen2.5:7b" if args.fast else ("qwen2.5:14b" if args.smart else "auto"),
        "use_vision": not args.no_vision, "use_review": not args.no_review,
        "tighten_pauses": not (args.no_tighten or args.ready), "punch_in": not (args.no_punch or args.ready),
        "music": not args.no_music and (not args.ready or bool(args.music)), "music_path": args.music,
        "captions": not args.no_captions, "ready_clip": args.ready,
        "shorts_framing": args.framing,
        "make_shorts": args.only in (None, "shorts"), "make_landscape": args.only in (None, "landscape"),
    })
    last = {"t": 0.0}
    if is_url(args.video):
        slug = re.sub(r"[^\w-]+", "-", args.video.split("://", 1)[1])[:40].strip("-")
        out = args.out or Path(__file__).parent / "jobs" / f"cli-{slug}"

        def dl_progress(frac: float, msg: str) -> None:
            if time.time() - last["t"] > 1.5 or frac >= 1:
                last["t"] = time.time()
                print(f"[download {frac * 100:5.1f}%] {msg}")

        meta = download(args.video, out / "download", dl_progress)
        print(f"  · Downloaded “{meta['title']}”")
        video = Path(meta["path"])
    else:
        video = Path(args.video)
        out = args.out or Path(__file__).parent / "jobs" / f"cli-{video.stem[:40]}"

    def update(**f):
        if f.get("log"):
            print(f"  · {f['log']}")
        if "message" in f and time.time() - last["t"] > 1.5 or f.get("stage") == "render":
            last["t"] = time.time()
            print(f"[{f.get('progress', 0):5.1f}%] {f.get('message', '')}")

    result = run_pipeline(video.resolve(), out, opts, update)
    print("\nClips:")
    for i, c in enumerate(result["clips"], 1):
        print(f" {i}. [{c['start']:.1f}s - {c['end']:.1f}s] score {c['score']:.0f}  {c['title']}")
    print(f"\nOutput folder: {out / 'clips'}")
    (out / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
