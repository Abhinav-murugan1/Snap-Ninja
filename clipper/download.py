"""Download a video from a link (YouTube, Kick, Twitch VODs and the other sites yt-dlp supports)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

ProgressFn = Callable[[float, str], None]

URL_RE = re.compile(r"^https?://\S+$", re.I)


class DownloadError(RuntimeError):
    pass


def is_url(text: str) -> bool:
    return bool(URL_RE.match((text or "").strip()))


def _size(b: float | None) -> str:
    if not b:
        return "?"
    if b >= 1e9:
        return f"{b / 1e9:.2f} GB"
    return f"{b / 1e6:.0f} MB" if b >= 1e6 else "<1 MB"


class _QuietLogger:
    """yt-dlp prints errors to stderr even when quiet; we report them ourselves."""
    def debug(self, msg: str) -> None: pass
    def info(self, msg: str) -> None: pass
    def warning(self, msg: str) -> None: pass
    def error(self, msg: str) -> None: pass


def _friendly(exc: Exception) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(exc)).replace("ERROR: ", "").strip()
    msg = re.sub(r"^\[[^\]]+\] [^:]+: ", "", msg)  # drop "[youtube] abc123: " prefix
    msg = re.sub(r" \(caused by .*\)$", "", msg)
    if "HTTP Error 404" in msg:
        msg = "Couldn't open that link: the page was not found. Check the address."
    elif "Unsupported URL" in msg:
        msg = "This site isn't supported. Try a YouTube, Kick or Twitch video link."
    elif "Private video" in msg or "Sign in" in msg or "members-only" in msg.lower():
        msg = "This video needs a login (private, members-only or age-restricted), so it can't be downloaded."
    elif "is offline" in msg.lower() or "not currently live" in msg.lower():
        msg = "This channel isn't live right now. Paste the link while the stream is on, or use a VOD link."
    return msg[:400]


def inspect_link(url: str, max_height: int = 1080) -> dict:
    """Look at a link without downloading. Returns title/uploader/duration/url and, for a stream that is
    live right now, is_live=True plus stream_url/headers that ffmpeg can record from."""
    import yt_dlp

    opts = {
        # live: muxed HLS (Twitch/Kick) or separate video + audio HLS (YouTube)
        "format": f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b",
        "noplaylist": True, "quiet": True, "no_warnings": True, "logger": _QuietLogger(),
        "js_runtimes": {"deno": {}, "node": {}},
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadError(_friendly(exc)) from None
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise DownloadError("This link is a playlist with no videos in it.")
        info = entries[0]
    status = info.get("live_status") or ("is_live" if info.get("is_live") else "not_live")
    if status == "is_upcoming":
        raise DownloadError("This stream hasn't started yet. Paste the link again once it's live.")
    meta = {
        "title": info.get("title") or "Video",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": info.get("duration") or 0,
        "url": info.get("webpage_url") or url,
        "is_live": status == "is_live",
        # which site and which stream: the live-chat reader needs these
        "site": (info.get("extractor_key") or "").lower(),
        "id": info.get("id") or "",
        "channel": info.get("uploader_id") or info.get("channel_id") or "",
    }
    if meta["is_live"]:
        formats = info.get("requested_formats") or [info]
        meta["stream_urls"] = [f.get("url") for f in formats if f.get("url")]
        meta["headers"] = (formats[0].get("http_headers") or info.get("http_headers") or {})
        if not meta["stream_urls"]:
            raise DownloadError("Couldn't find a recordable video stream for this live link.")
    return meta


def download(url: str, dest_dir: Path, progress: ProgressFn | None = None, max_height: int = 1080) -> dict:
    """Download `url` into dest_dir as source.<ext>. Returns {"path", "title", "uploader", "duration", "url"}."""
    import yt_dlp

    dest_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(dest_dir.glob("source.*"))
    existing = [p for p in existing if p.suffix not in (".part", ".ytdl") and p.stat().st_size > 0]

    state = {"part": 0, "last": ""}

    def hook(d: dict) -> None:
        if not progress:
            return
        if d.get("status") == "downloading":
            done = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                frac = done / total
            elif d.get("fragment_count"):
                frac = (d.get("fragment_index") or 0) / d["fragment_count"]
            else:
                frac = 0.0
            name = d.get("info_dict", {}).get("format_id", "")
            if name != state["last"]:
                state["last"] = name
                state["part"] += 1
            part = f", part {state['part']}" if state["part"] > 1 else ""
            progress(min(frac, 1.0), f"Downloading {frac * 100:.0f}% ({_size(done)} of {_size(total)}{part})")
        elif d.get("status") == "finished":
            progress(1.0, "Download finished, preparing the video")

    opts = {
        "format": f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(dest_dir / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "concurrent_fragment_downloads": 4,
        "retries": 5,
        "fragment_retries": 10,
        "progress_hooks": [hook],
        "logger": _QuietLogger(),
        # YouTube needs a JavaScript runtime to unlock its formats; yt-dlp uses whichever is installed
        "js_runtimes": {"deno": {}, "node": {}},
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            if progress:
                progress(0.0, "Reading link")
            info = ydl.extract_info(url, download=False)
            if info.get("_type") == "playlist":
                entries = [e for e in (info.get("entries") or []) if e]
                if not entries:
                    raise DownloadError("This link is a playlist with no videos in it.")
                info = entries[0]
            if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
                raise DownloadError("This is a live or upcoming stream. Wait until it ends, then use the link "
                                    "to the saved broadcast (VOD).")
            meta = {
                "title": info.get("title") or "Downloaded video",
                "uploader": info.get("uploader") or info.get("channel") or "",
                "duration": info.get("duration") or 0,
                "url": info.get("webpage_url") or url,
            }
            if existing:  # job restarted after a finished download
                return {"path": str(existing[0]), **meta}
            result = ydl.process_ie_result(info, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadError(_friendly(exc)) from None

    paths = [d.get("filepath") for d in (result.get("requested_downloads") or []) if d.get("filepath")]
    path = Path(paths[0]) if paths else next((p for p in dest_dir.glob("source.*") if p.suffix != ".part"), None)
    if not path or not Path(path).is_file():
        raise DownloadError("Download finished but the video file was not found.")
    return {"path": str(path), **meta}
