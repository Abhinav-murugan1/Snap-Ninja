"""Clipper web app.  Run:  .venv\\Scripts\\python app.py   then open http://127.0.0.1:7860"""
from __future__ import annotations

import json
import math
import os
import re
import uuid
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, request, send_from_directory

from clipper.env import claude_configured, load_env
from clipper.paths import configure_runtime, data_root, frozen, picker_command, resource_root

configure_runtime()
load_env()  # ANTHROPIC_API_KEY from .env, before anything creates a client

import threading  # noqa: E402

from clipper import youtube  # noqa: E402
from clipper.download import is_url  # noqa: E402
from clipper.publish import build_metadata  # noqa: E402
from clipper.jobs import JobStore  # noqa: E402
from clipper.music import MUSIC_EXTS, list_tracks
from clipper.options import Options
from clipper.output import clips_directory, validate_destination
from clipper.pipeline import MUSIC_DIR

ROOT = data_root()
UPLOADS = ROOT / "uploads"
STATIC = resource_root() / "static"
UPLOADS.mkdir(parents=True, exist_ok=True)
MUSIC_DIR.mkdir(parents=True, exist_ok=True)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".ts", ".wmv", ".mpg", ".mpeg"}

app = Flask(__name__, static_folder=None)
store = JobStore(ROOT / "jobs")


@app.before_request
def protect_local_app():
    """Reject DNS rebinding and browser requests from other websites.

    This application can read local media and delete jobs. Keep it loopback-only;
    the non-simple header forces cross-origin browsers through a denied preflight.
    """
    if urlsplit(request.host_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        return jsonify(error="Snap Ninja accepts local connections only."), 403
    origin = request.headers.get("Origin")
    if origin and origin != request.host_url.rstrip("/"):
        return jsonify(error="Requests from another website are not allowed."), 403
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return jsonify(error="Requests from another website are not allowed."), 403
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if request.headers.get("X-Snap-Ninja") != "1":
            return jsonify(error="Open Snap Ninja to perform this action."), 403
        if request.is_json and not isinstance(request.get_json(silent=True), dict):
            return jsonify(error="Expected a JSON object."), 400


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/desktop-instance")
def desktop_instance():
    return jsonify(app="Snap Ninja", data_root=str(ROOT.resolve()))


@app.get("/api/setup")
def setup_status():
    """Read-only checks: never download models or start an installer."""
    from clipper import brain

    try:
        tags = brain._request("/api/tags", timeout=3)
        models = {m.get("name", "") for m in tags.get("models", [])}
        ollama = True
    except brain.BrainError:
        models, ollama = set(), False
    return jsonify(packaged=frozen(), python=".".join(map(str, sys.version_info[:3])),
                   ffmpeg=bool(shutil.which("ffmpeg")), ffprobe=bool(shutil.which("ffprobe")),
                   runtime=bool(shutil.which("node") or shutil.which("deno")),
                   ollama=ollama, fast=brain.DEFAULT_MODEL in models,
                   smart=brain.SMART_MODEL in models, vision=brain.VISION_MODEL in models,
                   advanced=bool({brain.ADVANCED_MODEL, "qwen3.8:latest"} & models),
                   models={"fast": brain.DEFAULT_MODEL, "smart": brain.SMART_MODEL,
                           "vision": brain.VISION_MODEL, "advanced": brain.ADVANCED_MODEL})


def _safe_name(name: str) -> str:
    name = Path(name).name
    return re.sub(r"[^\w.\- ]", "_", name)[:120] or "upload"


_folder_picker_lock = threading.Lock()


@app.post("/api/output/validate")
def output_destination():
    data = request.get_json(silent=True) or {}
    try:
        path = validate_destination(str(data.get("path") or ""), ROOT / "jobs")
    except (ValueError, OSError) as exc:
        return jsonify(error=f"Cannot use that output folder: {exc}"), 400
    return jsonify(path=path, display=path or str(ROOT / "jobs"))


@app.post("/api/output/browse")
def browse_output():
    """Use an isolated native picker so Tk never runs in a Flask worker thread."""
    import subprocess
    import tempfile

    if not _folder_picker_lock.acquire(blocking=False):
        return jsonify(error="A folder picker is already open."), 409
    try:
        # Windowed executables have no stdout. A private temporary result file
        # also avoids re-launching the desktop app as though it were Python.
        with tempfile.TemporaryDirectory(prefix="snap-ninja-picker-") as folder:
            result = Path(folder) / "result.json"
            proc = subprocess.run(picker_command(result),
                                  capture_output=True, text=True, encoding="utf-8", timeout=300,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if proc.returncode:
                return jsonify(error="The folder picker is unavailable. Paste a folder path instead."), 503
            raw = result.read_text(encoding="utf-8") if result.exists() else proc.stdout
            path = json.loads(raw).get("path", "")
        return jsonify(path=path, cancelled=not bool(path))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return jsonify(error="The folder picker could not finish. Paste a folder path instead."), 503
    finally:
        _folder_picker_lock.release()


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/static/<path:name>")
def static_files(name: str):
    return send_from_directory(STATIC, name)


@app.post("/api/upload")
def upload():
    """Raw streaming upload (body = file bytes) so multi-GB videos never sit in memory or a temp copy."""
    name = _safe_name(request.args.get("name", "upload.mp4"))
    ext = Path(name).suffix.lower()
    kind = request.args.get("kind", "video")
    allowed = VIDEO_EXTS if kind == "video" else MUSIC_EXTS
    if ext not in allowed:
        return jsonify(error=f"Unsupported file type '{ext}'. Use one of: {', '.join(sorted(allowed))}"), 400
    upload_id = uuid.uuid4().hex[:12]
    dest = UPLOADS / f"{upload_id}{ext}"
    size = 0
    with open(dest, "wb") as f:
        while True:
            chunk = request.stream.read(4 * 1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            size += len(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        return jsonify(error="Upload was empty."), 400
    return jsonify(upload_id=dest.name, name=name, size=size)


@app.post("/api/jobs")
def create_job():
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data.get("options", {}), dict):
        return jsonify(error="Options must be a JSON object."), 400
    opts_in = dict(data.get("options") or {})
    try:
        opts_in["output_dir"] = validate_destination(str(opts_in.get("output_dir") or ""), ROOT / "jobs")
    except (ValueError, OSError) as exc:
        return jsonify(error=f"Cannot use that output folder: {exc}"), 400
    url = ""
    if data.get("path") and is_url(str(data["path"])):
        data["url"] = str(data.pop("path")).strip()
    if data.get("upload_id"):
        src = UPLOADS / _safe_name(data["upload_id"])
        name = data.get("name") or src.name
        owns = True
    elif data.get("url"):
        url = str(data["url"]).strip()
        if not is_url(url):
            return jsonify(error="That link doesn't look right. Paste the full address, starting with https://"), 400
        src, name, owns = None, url, True
    elif data.get("path"):
        src = Path(str(data["path"]).strip().strip('"'))
        name = src.name
        owns = False
        if src.is_dir() and opts_in.get("ready_clip"):  # a folder of finished clips: one job per clip
            files = sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
            if not files:
                return jsonify(error=f"No videos found in {src}"), 400
            opts = Options.from_dict(opts_in)
            jobs = [store.create(p, p.name, opts, False) for p in files]
            return jsonify({**jobs[0], "more": len(jobs) - 1}), 201
    else:
        return jsonify(error="Add a video file, a video link, or a path to a file."), 400
    if src is not None:
        if not src.is_file():
            return jsonify(error=f"File not found: {src}"), 400
        if src.suffix.lower() not in VIDEO_EXTS:
            return jsonify(error=f"Unsupported video type '{src.suffix}'."), 400
    if data.get("music_upload_id"):
        mp = UPLOADS / _safe_name(data["music_upload_id"])
        if mp.is_file():
            opts_in["music_path"] = str(mp)
    elif opts_in.get("music_track"):
        mp = MUSIC_DIR / _safe_name(opts_in.pop("music_track"))
        if mp.is_file():
            opts_in["music_path"] = str(mp)
    job = store.create(src, name, Options.from_dict(opts_in), owns, url=url)
    return jsonify(job), 201


@app.get("/api/jobs")
def list_jobs():
    """?sizes=1 adds how much disk space each job takes (the Previous jobs menu)."""
    return jsonify(store.list(sizes=request.args.get("sizes") == "1"))


@app.delete("/api/jobs/<job_id>")
def delete_job(job_id: str):
    """Delete a job at once; it can be undone for a minute (POST /api/jobs/<id>/restore)."""
    try:
        return jsonify(store.trash(job_id))
    except KeyError:
        abort(404)
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 409


@app.post("/api/jobs/<job_id>/restore")
def restore_job(job_id: str):
    if not re.fullmatch(r"[\w\-]+", job_id):
        abort(404)
    try:
        return jsonify(store.restore(job_id))
    except (KeyError, OSError):
        return jsonify(error="It's too late to undo that."), 410


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str):
    job = store.get(job_id)
    if not job:
        abort(404)
    return jsonify({**job, "output_path": str(_job_clips(job))})


@app.post("/api/jobs/<job_id>/stop")
def stop_job(job_id: str):
    if not store.request_stop(job_id):
        return jsonify(error="This job isn't recording."), 409
    return jsonify(ok=True)


@app.get("/api/jobs/<job_id>/source")
def source_info(job_id: str):
    info = store.source_info(job_id)
    if info is None:
        abort(404)
    return jsonify(info)


@app.post("/api/jobs/<job_id>/source/delete")
def delete_source(job_id: str):
    try:
        res = store.delete_source(job_id)
    except KeyError:
        abort(404)
    except (RuntimeError, OSError) as exc:
        return jsonify(error=f"Couldn't delete the original video: {exc}"), 409
    return jsonify(res)


@app.post("/api/jobs/<job_id>/clips/<int:index>/edit")
def edit_clip(job_id: str, index: int):
    """Adjust a finished clip by hand: {"start", "end", "title", "hook"}; it is rendered again."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        start, end = float(data["start"]), float(data["end"])
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("Set a finite start and end time.")
        clip = store.edit_clip(job_id, index, start, end,
                               str(data.get("title") or ""), str(data.get("hook") or ""))
    except KeyError:
        abort(404)
    except (TypeError, ValueError) as exc:
        return jsonify(error=str(exc) if str(exc) and "could not convert" not in str(exc)
                       else "Set a start and an end."), 400
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 409
    return jsonify(clip), 202


@app.post("/api/jobs/<job_id>/open-folder")
def open_folder(job_id: str):
    """Show the job's finished clips in File Explorer (Clipper runs on this computer)."""
    job = store.get(job_id)
    if not job:
        abort(404)
    folder = _job_clips(job)
    if not re.fullmatch(r"[\w\-]+", job_id) or not folder.is_dir():
        abort(404)
    if not hasattr(os, "startfile"):
        return jsonify(error="Opening folders works on Windows only."), 501
    os.startfile(folder)  # noqa: S606 - a fixed folder inside Clipper's own jobs directory
    return jsonify(ok=True)


@app.get("/api/jobs/<job_id>/original")
def original_video(job_id: str):
    """The job's original video, streamed with range requests, for the clip editor's player."""
    from flask import send_file

    job = store.get(job_id)
    if not job or not job.get("source") or job.get("source_deleted"):
        abort(404)
    src = Path(job["source"])
    if not src.is_file():
        abort(404)
    return send_file(src, conditional=True, max_age=0)


@app.get("/api/jobs/<job_id>/lines")
def transcript_lines(job_id: str):
    """Transcript lines between ?from= and ?to= seconds, for picking cut points in the clip editor."""
    if not re.fullmatch(r"[\w\-]+", job_id):
        abort(404)
    path = ROOT / "jobs" / job_id / "work" / "transcript.json"
    if not path.exists():
        return jsonify(lines=[])
    try:
        t0, t1 = float(request.args.get("from", 0)), float(request.args.get("to", 1e9))
        if not math.isfinite(t0) or not math.isfinite(t1):
            raise ValueError
    except ValueError:
        return jsonify(error="Use finite numbers for transcript times."), 400
    segs = json.loads(path.read_text(encoding="utf-8")).get("segments", [])
    return jsonify(lines=[{"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"].strip()}
                          for s in segs if t0 <= s["start"] <= t1 and s["text"].strip()])


@app.post("/api/jobs/<job_id>/clips/<int:index>/vote")
def vote_clip(job_id: str, index: int):
    """👍/👎 on a clip. Remembered across jobs so the local AI learns what you like."""
    job = store.get(job_id)
    if not job or not (0 <= index < len(job.get("clips", []))):
        abort(404)
    vote = (request.get_json(force=True, silent=True) or {}).get("vote")
    if vote not in ("up", "down", None):
        return jsonify(error="Vote must be up, down or none."), 400
    from clipper import feedback
    from clipper.publish import _clip_words

    text = " ".join(_clip_words(ROOT / "jobs" / job_id, job["clips"][index]))
    feedback.set_vote(job, index, vote, text)
    store.set_clip_field(job_id, index, vote=vote)
    return jsonify(ok=True, vote=vote, totals=feedback.counts())


# ---------------------------------------------------------------------------- YouTube

@app.get("/api/youtube/status")
def youtube_status():
    return jsonify(youtube.status())


@app.post("/api/youtube/client")
def youtube_client():
    try:
        youtube.save_client_file(request.get_data(cache=False))
    except youtube.YouTubeError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(youtube.status())


@app.post("/api/youtube/connect")
def youtube_connect():
    try:
        youtube.connect()
    except youtube.YouTubeError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(youtube.status())


@app.post("/api/youtube/disconnect")
def youtube_disconnect():
    youtube.disconnect()
    return jsonify(youtube.status())


def _job_clips(job: dict) -> Path:
    return clips_directory(ROOT / "jobs" / job["id"], (job.get("options") or {}).get("output_dir", ""))


def _clip_file(job: dict, index: int, kind: str) -> Path | None:
    try:
        name = job["clips"][index]["files"].get(kind)
    except (IndexError, KeyError, TypeError):
        return None
    path = _job_clips(job) / name if name else None
    return path if path and path.is_file() else None


@app.get("/api/jobs/<job_id>/clips/<int:index>/youtube")
def youtube_metadata(job_id: str, index: int):
    job = store.get(job_id)
    kind = request.args.get("kind", "short")
    if not job or not _clip_file(job, index, kind):
        abort(404)
    return jsonify(build_metadata(job, job["clips"][index], kind, ROOT / "jobs" / job_id))


@app.post("/api/jobs/<job_id>/clips/<int:index>/youtube")
def youtube_upload(job_id: str, index: int):
    job = store.get(job_id)
    data = request.get_json(force=True, silent=True) or {}
    kind = data.get("kind", "short")
    if not job:
        abort(404)
    video = _clip_file(job, index, kind)
    if not video:
        return jsonify(error="That clip file isn't ready."), 400
    title = str(data.get("title", "")).strip()
    if not title:
        return jsonify(error="Add a title."), 400
    if data.get("privacy") not in ("public", "unlisted", "private"):
        return jsonify(error="Choose who can see the video."), 400
    current = (job["clips"][index].get("youtube") or {}).get(kind, {})
    if current.get("status") == "uploading":
        return jsonify(error="This clip is already uploading."), 409
    if not youtube.status()["connected"]:
        return jsonify(error="Connect a YouTube channel first."), 400
    tags = data.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    meta = {"title": title, "description": str(data.get("description", "")), "tags": tags[:40],
            "category_id": str(data.get("category_id", "24")), "privacy": data["privacy"], "kind": kind}

    def run():
        store.set_clip_upload(job_id, index, kind, status="uploading", progress=0, error=None, title=title)
        last = {"p": -1}

        def prog(f: float):
            p = int(f * 100)
            if p != last["p"]:
                last["p"] = p
                store.set_clip_upload(job_id, index, kind, progress=p)

        try:
            res = youtube.upload(video, meta, prog)
            store.set_clip_upload(job_id, index, kind, status="done", progress=100, **res)
        except Exception as exc:
            store.set_clip_upload(job_id, index, kind, status="failed", error=str(exc))

    threading.Thread(target=run, daemon=True, name=f"yt-{job_id}-{index}-{kind}").start()
    return jsonify(ok=True), 202


@app.get("/api/music")
def music_tracks():
    return jsonify([p.name for p in list_tracks(MUSIC_DIR)])


@app.get("/api/status")
def status():
    from clipper.transcribe import _cuda_available, _register_cuda_dlls

    _register_cuda_dlls()
    from clipper import brain

    have = brain.installed()
    model = brain.pick_model(request.args.get("model", "auto"))
    ready, msg = brain.available(model)
    from clipper import feedback

    return jsonify(cuda=_cuda_available(), claude=claude_configured(), brain=ready, brain_message=msg, model=model,
                   smart=brain.SMART_MODEL in have, fast=brain.DEFAULT_MODEL in have,
                   vision=brain.pick_vision_model(model) in have, votes=feedback.counts())


@app.post("/api/jobs/<job_id>/cut")
def cut_live(job_id: str):
    """Cut a clip by hand from a live recording: {"start": s, "end": s} on the recording's clock, or
    {"last": seconds} for the most recent stretch."""
    from clipper.live import playlist_segments

    data = request.get_json(force=True, silent=True) or {}
    if not re.fullmatch(r"[\w\-]+", job_id):
        abort(404)
    segs = playlist_segments(ROOT / "jobs" / job_id / "live" / "index.m3u8")
    recorded = segs[-1][1] + segs[-1][2] if segs else 0.0
    try:
        if data.get("last"):
            end = recorded
            start = max(0.0, end - float(data["last"]))
        else:
            start, end = float(data["start"]), float(data["end"])
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("Set a finite start and end time.")
        start, end = max(0.0, min(start, end)), min(recorded, max(start, end))
        child = store.cut_live(job_id, start, end)
    except KeyError:
        abort(404)
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc) or "Mark a start and an end first."), 400
    except Exception as exc:  # ffmpeg failed
        return jsonify(error=f"Couldn't cut that clip: {str(exc)[:200]}"), 500
    return jsonify(child), 201


@app.get("/live/<job_id>/<name>")
def live_file(job_id: str, name: str):
    """The live recording as HLS (index.m3u8 + segments), for the in-page player."""
    if not re.fullmatch(r"[\w\-]+", job_id) or not re.fullmatch(r"(index\.m3u8|seg_\d+\.ts)", name):
        abort(404)
    resp = send_from_directory(ROOT / "jobs" / job_id / "live", name, conditional=name != "index.m3u8")
    if name == "index.m3u8":
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/files/<job_id>/<path:name>")
def job_file(job_id: str, name: str):
    if not re.fullmatch(r"[\w\-]+", job_id):
        abort(404)
    job = store.get(job_id)
    if not job:
        abort(404)
    return send_from_directory(_job_clips(job), name, conditional=True)


if __name__ == "__main__":
    from clipper.winproc import children_die_with_me

    children_die_with_me()  # closing this window also stops ffmpeg (a live recording would go on otherwise)
    port = int(os.environ.get("PORT", "7860"))
    print(f"Clipper running at http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)
