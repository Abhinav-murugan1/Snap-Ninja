"""Job store + single background worker (one render at a time keeps GPU/CPU use sane)."""
from __future__ import annotations

import json
import queue
import threading
import time
import traceback
import uuid
from pathlib import Path

from .download import download, inspect_link
from .live import LiveDVR, LiveRecorder, Part, cut_recording
from .options import Options
from .pipeline import rerender_clip, run_pipeline


class JobStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.q: queue.Queue[str] = queue.Queue()
        self.stops: dict[str, threading.Event] = {}
        self.edits: queue.Queue[tuple[str, int]] = queue.Queue()
        self.cut_lock = threading.Lock()
        self._load()
        self.purge_trash(keep=0)  # jobs deleted before Clipper was closed
        threading.Thread(target=self._worker, daemon=True, name="clipper-worker").start()
        # clip re-edits are short: their own worker, so they don't wait behind a long job
        threading.Thread(target=self._edit_worker, daemon=True, name="clipper-edits").start()

    # ------------------------------------------------------------------ persistence
    def _path(self, job_id: str) -> Path:
        return self.root / job_id / "job.json"

    def _load(self) -> None:
        for p in self.root.glob("*/job.json"):
            try:
                job = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for c in job.get("clips") or []:  # a re-edit that was cut off by a restart
                if c.pop("editing", None):
                    c["edit_error"] = "Clipper was closed during this edit. Adjust it again."
            if job.get("status") in ("queued", "running"):
                if (job.get("live") or {}).get("manual"):
                    job["status"] = "done"
                    job["live"]["recording"] = False
                    if job["live"].get("chat"):  # the chat reader stopped with Clipper
                        job["live"]["chat"]["on"] = False
                    job["message"] = ("Clipper was closed, so recording stopped. "
                                      "You can still cut clips from what was recorded.")
                elif job.get("live"):
                    job["status"] = "done" if job.get("clips") else "failed"
                    job["live"]["recording"] = False
                    job["message"] = "Clipper was closed, so live recording stopped. Clips made so far are kept."
                    job["error"] = None if job.get("clips") else job["message"]
                else:
                    job["status"] = "failed"
                    job["error"] = "Server restarted while this job was running. Start it again."
            self.jobs[job["id"]] = job

    def _save(self, job: dict) -> None:
        p = self._path(job["id"])
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    # ------------------------------------------------------------------ api
    def create(self, source: Path | None, name: str, opts: Options, owns_source: bool, url: str = "") -> dict:
        job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        job = {
            "id": job_id, "name": name, "source": str(source) if source else "", "url": url,
            "owns_source": owns_source,
            "options": opts.to_dict(), "status": "queued", "stage": "queued", "progress": 0,
            "message": "Waiting to start", "log": [], "clips": [], "energy": [], "video": None,
            "created": time.time(), "error": None,
        }
        with self.lock:
            self.jobs[job_id] = job
            self._save(job)
        self.q.put(job_id)
        return job

    def get(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return json.loads(json.dumps(job)) if job else None

    def list(self, sizes: bool = False) -> list[dict]:
        with self.lock:
            items = [{k: j[k] for k in ("id", "name", "status", "progress", "created", "message")}
                     | {"clip_count": len(j.get("clips", []))} for j in self.jobs.values()]
            sources = {j["id"]: (j.get("source", ""), j.get("owns_source")) for j in self.jobs.values()}
        if sizes:
            for item in items:
                item["size"] = self.disk_use(item["id"], *sources[item["id"]])
        return sorted(items, key=lambda j: -j["created"])

    def disk_use(self, job_id: str, source: str = "", owns_source: bool = False) -> int:
        """Bytes this job takes on disk: its folder (clips, recording, downloaded video, working files), plus
        Clipper's own copy of an uploaded video, which lives in uploads/. A file of yours that the job only
        points at is not counted: deleting the job doesn't remove it."""
        import os

        total = 0
        for dirpath, _, files in os.walk(self.root / job_id):
            for name in files:
                try:
                    total += os.stat(os.path.join(dirpath, name)).st_size
                except OSError:
                    pass
        src = Path(source) if source else None
        uploads = self.root.parent / "uploads"
        try:
            if owns_source and src and src.is_file() and uploads.resolve() in src.resolve().parents:
                total += src.stat().st_size
        except OSError:
            pass
        return total

    def update(self, job_id: str, **fields) -> None:
        with self.lock:
            job = self.jobs[job_id]
            msg = fields.pop("log", None)
            if msg:
                job["log"].append({"t": time.time(), "msg": msg})
            if "message" in fields and fields["message"] and fields["message"] != job.get("message"):
                job["log"].append({"t": time.time(), "msg": fields["message"]})
            job["log"] = job["log"][-300:]
            job.update(fields)
            self._save(job)

    # ------------------------------------------------------------------ worker
    def _worker(self) -> None:
        while True:
            job_id = self.q.get()
            job = self.get(job_id)
            if not job:
                continue
            self.update(job_id, status="running", started=time.time())
            try:
                opts = Options.from_dict(job["options"])
                if job.get("url") and not job.get("source"):
                    self.update(job_id, stage="download", progress=0, message="Reading link")
                    meta = inspect_link(job["url"])
                    if meta["is_live"]:
                        # live jobs run for hours: give them their own thread so the queue keeps moving
                        run = self._run_live_manual if opts.live_manual else self._run_live
                        threading.Thread(target=run, args=(job_id, meta, opts), daemon=True,
                                         name=f"live-{job_id}").start()
                        continue
                    if opts.live_manual:
                        raise RuntimeError("This link isn't live right now. Cutting clips by hand needs a "
                                           "stream that is live; for a finished video, use the normal mode.")
                    self.update(job_id, stage="download", progress=0, message="Reading link")
                    meta = download(job["url"], self.root / job_id / "download",
                                    lambda f, m: self.update(job_id, stage="download",
                                                             progress=round(100 * f, 1), message=m))
                    self.update(job_id, source=meta["path"], name=meta["title"], source_info=meta,
                                log=f"Downloaded “{meta['title']}”" + (f" by {meta['uploader']}" if meta["uploader"] else ""))
                    job = self.get(job_id)
                run_pipeline(Path(job["source"]), self.root / job_id, opts,
                             lambda **f: self.update(job_id, **f))
                self.update(job_id, status="done", progress=100, finished=time.time())
            except Exception as exc:
                traceback.print_exc()
                self.update(job_id, status="failed", error=_error_text(exc), message="Failed", finished=time.time())

    # ------------------------------------------------------------------ original video
    def _source_files(self, job: dict) -> tuple[str, list[Path]]:
        """(kind, files) for the job's original video.
        kind: "recording" (live parts), "copy" (an upload or download Clipper made), "yours" (a file you pointed at)."""
        job_dir = self.root / job["id"]
        if job.get("live"):
            return "recording", sorted((job_dir / "live").glob("*.ts"))
        src = Path(job["source"]) if job.get("source") else None
        if not src:
            return "copy", []
        uploads = self.root.parent / "uploads"
        inside = lambda base: base.resolve() in src.resolve().parents  # noqa: E731
        if inside(uploads) or inside(job_dir):
            extra = [p for p in (job_dir / "download").glob("source.*")] if inside(job_dir) else []
            files = {src, *extra}
            return "copy", sorted(p for p in files if p.exists())
        return "yours", [src] if src.exists() else []

    def source_info(self, job_id: str) -> dict | None:
        job = self.get(job_id)
        if not job:
            return None
        kind, files = self._source_files(job)
        work = list((self.root / job_id / "work").rglob("audio16k.wav"))
        size = sum(p.stat().st_size for p in files + work)
        busy = job["status"] in ("queued", "running")
        return {"kind": kind, "files": len(files), "size": size, "name": job.get("name", ""),
                "path": str(files[0]) if kind == "yours" and files else "",
                "available": bool(files) and not busy, "busy": busy,
                "deleted": bool(job.get("source_deleted"))}

    def delete_source(self, job_id: str) -> dict:
        """Delete the original video: Clipper's own copies are removed, your own file goes to the Recycle Bin.
        The working audio copy goes too. Clips, transcript and job data stay."""
        job = self.get(job_id)
        if not job:
            raise KeyError(job_id)
        if job["status"] in ("queued", "running"):
            raise RuntimeError("This job is still working. Wait until it's finished.")
        kind, files = self._source_files(job)
        freed = 0
        for p in files:
            size = p.stat().st_size
            if kind == "yours":
                from send2trash import send2trash
                send2trash(str(p))
            else:
                p.unlink()
            freed += size
        for wav in (self.root / job_id / "work").rglob("audio16k.wav"):
            freed += wav.stat().st_size
            wav.unlink()
        where = "moved to the Recycle Bin" if kind == "yours" else "deleted"
        gb = freed / 1e9
        self.update(job_id, source_deleted=True, source_deleted_at=time.time(),
                    log=f"Original video {where}; freed {gb:.2f} GB" if gb >= 0.1 else
                        f"Original video {where}; freed {freed / 1e6:.0f} MB")
        return {"kind": kind, "freed": freed}

    def set_clip_field(self, job_id: str, index: int, **fields) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job["clips"][index].update(fields)
            self._save(job)

    def set_clip_upload(self, job_id: str, index: int, kind: str, **fields) -> None:
        """Record YouTube upload state on a clip: clips[index]["youtube"][kind] = {...}."""
        with self.lock:
            job = self.jobs[job_id]
            clip = job["clips"][index]
            yt = clip.setdefault("youtube", {})
            yt[kind] = {**yt.get(kind, {}), **fields}
            self._save(job)

    # ------------------------------------------------------------------ delete a job (with undo)
    TRASH_KEEP = 60.0  # seconds a deleted job can still be restored

    def trash(self, job_id: str) -> dict:
        """Delete a job right away from the list, but keep its folder in jobs/_trash for a minute so the
        page can undo it. After that the folder (clips, recording, Clipper's copy of the video) is removed."""
        import shutil

        job = self.get(job_id)
        if not job:
            raise KeyError(job_id)
        if job["status"] in ("queued", "running"):
            raise RuntimeError("This job is still working. Stop it or let it finish, then delete it."
                               if not job.get("live") else "Stop the recording first, then delete it.")
        if any(c.get("editing") for c in job.get("clips") or []):
            raise RuntimeError("A clip of this job is being re-edited. Delete it when that's done.")
        trash = self.root / "_trash"
        trash.mkdir(exist_ok=True)
        dest = trash / job_id
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        try:
            shutil.move(str(self.root / job_id), str(dest))
        except OSError as exc:
            raise RuntimeError("Windows is still using a file of this job (a video playing somewhere?). "
                               "Close it and try again.") from exc
        (dest / ".trashed").write_text(str(time.time()), encoding="utf-8")
        with self.lock:
            self.jobs.pop(job_id, None)
        threading.Timer(self.TRASH_KEEP + 5, self.purge_trash).start()
        return {"id": job_id, "name": job.get("name", "")}

    def restore(self, job_id: str) -> dict:
        """Undo a delete made in the last minute."""
        import shutil

        src = self.root / "_trash" / job_id
        if not (src / "job.json").exists():
            raise KeyError(job_id)
        (src / ".trashed").unlink(missing_ok=True)
        shutil.move(str(src), str(self.root / job_id))
        job = json.loads((self.root / job_id / "job.json").read_text(encoding="utf-8"))
        with self.lock:
            self.jobs[job_id] = job
        return job

    def purge_trash(self, keep: float | None = None) -> None:
        """Remove deleted jobs for good once their undo time is over (all of them at start-up)."""
        import shutil

        keep = self.TRASH_KEEP if keep is None else keep
        trash = self.root / "_trash"
        if not trash.is_dir():
            return
        uploads = self.root.parent / "uploads"
        for d in trash.iterdir():
            mark = d / ".trashed"
            try:
                when = float(mark.read_text(encoding="utf-8")) if mark.exists() else 0.0
            except (OSError, ValueError):
                when = 0.0
            if time.time() - when < keep:
                continue
            try:  # Clipper's own copy of an uploaded video lives in uploads/, outside the job folder
                job = json.loads((d / "job.json").read_text(encoding="utf-8"))
                src = Path(job.get("source") or "")
                if job.get("owns_source") and src.is_file() and uploads.resolve() in src.resolve().parents:
                    src.unlink()
            except (OSError, ValueError):
                pass
            shutil.rmtree(d, ignore_errors=True)

    # ------------------------------------------------------------------ adjust a clip by hand
    def edit_clip(self, job_id: str, index: int, start: float, end: float, title: str, hook: str) -> dict:
        """Change a finished clip's range, title and hook; it is rendered again in the background."""
        job = self.get(job_id)
        if not job or not 0 <= index < len(job.get("clips") or []):
            raise KeyError(job_id)
        clip = job["clips"][index]
        if job["status"] in ("queued", "running") and not (job.get("live") or {}).get("manual"):
            raise RuntimeError("Wait until this job has finished, then adjust its clips.")
        if clip.get("part"):
            raise RuntimeError("Clips from an automatic live recording can't be adjusted. Use "
                               "“Live stream: cut clips by hand” to cut exactly what you want.")
        if clip.get("editing"):
            raise RuntimeError("This clip is being re-edited already.")
        if job.get("source_deleted") or not job.get("source") or not Path(job["source"]).exists():
            raise RuntimeError("The original video was deleted, so this clip can't be cut again.")
        dur = (job.get("video") or {}).get("duration") or 1e9
        start, end = max(0.0, float(start)), min(float(end), dur)
        if end - start < 3:
            raise ValueError("Make the clip at least 3 seconds long.")
        if end - start > 600:
            raise ValueError("Clips can be at most 10 minutes long.")
        with self.lock:
            c = self.jobs[job_id]["clips"][index]
            c.update(pending_edit={"start": start, "end": end, "title": title.strip()[:100] or c.get("title", ""),
                                   "hook": hook.strip()[:60]}, editing=True)
            c.pop("edit_error", None)
            self._save(self.jobs[job_id])
        self.edits.put((job_id, index))
        return self.get(job_id)["clips"][index]

    def _edit_worker(self) -> None:
        while True:
            job_id, index = self.edits.get()
            job = self.get(job_id)
            if not job:
                continue
            clip = job["clips"][index]
            want = clip.get("pending_edit") or {}
            try:
                rev = int(clip.get("rev", 0)) + 1
                new = rerender_clip(Path(job["source"]), self.root / job_id, Options.from_dict(job["options"]),
                                    {**clip, **want}, index, rev, lambda m: self.update(job_id, log=m))
                keep = {k: clip[k] for k in ("vote", "youtube") if k in clip}
                if keep.get("youtube"):  # what's on YouTube is the earlier cut
                    keep["youtube_outdated"] = True
                with self.lock:
                    j = self.jobs[job_id]
                    j["clips"][index] = {**new, **keep}
                    j["log"].append({"t": time.time(), "msg": f"Clip {index + 1} re-edited: "
                                                             f"{_hms(new['start'])}–{_hms(new['end'])}"})
                    self._save(j)
            except Exception as exc:
                traceback.print_exc()
                with self.lock:
                    c = self.jobs[job_id]["clips"][index]
                    c.pop("editing", None)
                    c["edit_error"] = f"Couldn't re-edit this clip: {_error_text(exc)[:200]}"
                    self._save(self.jobs[job_id])

    # ------------------------------------------------------------------ live streams
    def request_stop(self, job_id: str) -> bool:
        ev = self.stops.get(job_id)
        if not ev:
            return False
        ev.set()
        return True

    def _run_live(self, job_id: str, meta: dict, opts: Options) -> None:
        """Record the stream in parts; as each part completes, clip it (with an overlap from the
        previous part so moments on a part boundary survive) and add the clips to the job."""
        job_dir = self.root / job_id
        stop = self.stops.setdefault(job_id, threading.Event())
        part_s = max(60.0, opts.live_part_minutes * 60)
        part_opts = Options.from_dict({**opts.to_dict(), "num_clips": opts.live_clips_per_part})
        live = {"recording": True, "recorded": 0.0, "parts_done": 0, "parts_recorded": 0,
                "part_minutes": part_s / 60, "max_hours": opts.live_max_hours, "started": time.time()}
        self.update(job_id, name=meta["title"], source_info={k: meta[k] for k in ("title", "uploader", "url")},
                    live=live, stage="live", progress=0,
                    message=f"Recording live. First clips arrive after {part_s / 60:.0f} minutes.",
                    log=f"Live stream found: “{meta['title']}”" + (f" by {meta['uploader']}" if meta["uploader"] else ""))
        rec = LiveRecorder(self.get(job_id)["url"], meta, job_dir / "live", part_s,
                           lambda m: self.update(job_id, log=m))
        rec.start()
        deadline = time.time() + opts.live_max_hours * 3600
        done: list[Part] = []
        clock = 0.0  # seconds of recording before the next unprocessed part
        try:
            while True:
                if (stop.is_set() or time.time() > deadline) and not rec.end_reason:
                    rec.stop("Stopped by you" if stop.is_set() else f"Reached the {opts.live_max_hours:g} h limit")
                # ffmpeg lists a part only once it is closed, so every listed part is complete
                pending = [p for p in rec.parts() if p.index not in {d.index for d in done}]
                for part in pending:
                    prev = done[-1] if done else None
                    self._clip_part(job_id, part, prev, clock, part_opts)
                    clock += part.duration
                    done.append(part)
                    self._live_status(job_id, rec, len(done))
                if rec.ended and not [p for p in rec.parts() if p.index not in {d.index for d in done}]:
                    break
                self._live_status(job_id, rec, len(done))
                time.sleep(3)
            n = len(self.get(job_id)["clips"])
            self.update(job_id, status="done", progress=100, finished=time.time(),
                        live={**self.get(job_id)["live"], "recording": False},
                        message=f"{rec.end_reason or 'Recording finished'}. {n} clips from "
                                f"{clock / 60:.0f} min of stream.")
        except Exception as exc:
            traceback.print_exc()
            rec.stop("Error")
            self.update(job_id, status="failed", error=_error_text(exc), message="Failed", finished=time.time(),
                        live={**self.get(job_id)["live"], "recording": False})
        finally:
            self.stops.pop(job_id, None)

    # ------------------------------------------------------------------ live: cut clips by hand
    def _run_live_manual(self, job_id: str, meta: dict, opts: Options) -> None:
        """Record the stream as a seekable HLS playlist. The page plays it, the user marks clips, and
        cut_live() turns each mark into its own finished-clip job (captions, title, hook, upload)."""
        job_dir = self.root / job_id
        stop = self.stops.setdefault(job_id, threading.Event())
        if opts.chat_only:
            from .chat import chat_site

            if not chat_site(meta.get("url") or "", meta):
                raise RuntimeError("The chat hype clipper works with Twitch and YouTube live streams. For other "
                                   "sites, use “Live stream: cut clips by hand”.")
        live = {"recording": True, "manual": True, "recorded": 0.0, "cuts": [], "max_hours": opts.live_max_hours,
                "started": time.time(), "chat_only": opts.chat_only}
        self.update(job_id, name=meta["title"], source_info={k: meta[k] for k in ("title", "uploader", "url")},
                    live=live, stage="live", progress=0,
                    message=("Recording and watching chat. Clips appear below each time chat explodes."
                             if opts.chat_only else "Recording. Watch it here and mark the clips you want."),
                    log=f"Live stream found: “{meta['title']}”" + (f" by {meta['uploader']}" if meta["uploader"] else ""))
        rec = LiveDVR(self.get(job_id)["url"], meta, job_dir / "live", lambda m: self.update(job_id, log=m))
        rec.start()
        chat = _ChatClipper(self, job_id, meta, opts) if (opts.live_chat_clips or opts.chat_only) else None
        deadline = time.time() + opts.live_max_hours * 3600
        try:
            while not rec.ended:
                if (stop.is_set() or time.time() > deadline) and not rec.end_reason:
                    rec.stop("Stopped by you" if stop.is_set() else f"Reached the {opts.live_max_hours:g} h limit")
                recorded = rec.recorded_seconds()
                fields = {"recorded": round(recorded, 1)}
                if chat:  # watch chat and clip its explosions on its own
                    fields["chat"] = chat.tick(recorded)
                    if opts.chat_only and chat.reader and not chat.reader.is_alive() and not rec.end_reason:
                        # the hype clipper has nothing to watch any more (chat switched off or gone): stop recording
                        rec.stop(chat.last_note.rstrip(".") if "switched off" in chat.last_note
                                 else "The stream's chat stopped, so the hype clipper stopped")
                self.update(job_id, live={**self.get(job_id)["live"], **fields})
                time.sleep(2)
            if chat:  # spikes from the last minutes: the recording now covers them
                fields = {"chat": chat.finish(rec.recorded_seconds())}
                self.update(job_id, live={**self.get(job_id)["live"], **fields})
            job = self.get(job_id)
            n = len(job["live"].get("cuts", []))
            self.update(job_id, status="done", progress=100, finished=time.time(),
                        live={**job["live"], "recording": False, "recorded": round(rec.recorded_seconds(), 1)},
                        message=f"{rec.end_reason or 'Recording finished'}. {_hms(rec.recorded_seconds())} recorded, "
                                f"{n} clip{'' if n == 1 else 's'} cut. You can still cut more from the recording.")
        except Exception as exc:
            traceback.print_exc()
            rec.stop("Error")
            self.update(job_id, status="failed", error=_error_text(exc), message="Failed", finished=time.time(),
                        live={**self.get(job_id)["live"], "recording": False})
        finally:
            self.stops.pop(job_id, None)

    def cut_live(self, job_id: str, start: float, end: float, by: str = "hand", note: str = "") -> dict:
        """Cut [start, end] (seconds on the recording's clock) from a manual live job and queue it as its own
        finished-clip job: captions, AI title and hook, ready for Upload to YouTube. by: "hand" or "chat"."""
        with self.cut_lock:  # you and the chat clipper can cut at the same moment: one at a time
            return self._cut_live(job_id, start, end, by, note)

    def _cut_live(self, job_id: str, start: float, end: float, by: str, note: str) -> dict:
        job = self.get(job_id)
        if not job or not (job.get("live") or {}).get("manual"):
            raise KeyError(job_id)
        if end - start < 3:
            raise ValueError("Make the clip at least 3 seconds long.")
        if end - start > 600:
            raise ValueError("Clips can be at most 10 minutes long.")
        n = len(job["live"].get("cuts", [])) + 1
        dest = self.root / job_id / "cuts" / f"cut_{n:03d}.mp4"
        cut_recording(self.root / job_id / "live" / "index.m3u8", start, end, dest)
        base = Options.from_dict(job["options"])
        opts = Options.from_dict({**base.to_dict(), "ready_clip": True, "live_manual": False,
                                  "tighten_pauses": False, "punch_in": False})
        kind = "chat clip" if by == "chat" else "clip"
        child = self.create(dest, f"{job['name']}: {kind} {n} ({_hms(start)}–{_hms(end)})", opts, False)
        with self.lock:
            j = self.jobs[job_id]
            j["live"].setdefault("cuts", []).append({"n": n, "start": start, "end": end, "job": child["id"],
                                                     "name": child["name"], "by": by, "note": note})
            j["log"].append({"t": time.time(), "msg": f"Cut {kind} {n}: {_hms(start)}–{_hms(end)}"
                                                     + (f" ({note})" if note else "")})
            self._save(j)
        return child

    def _live_status(self, job_id: str, rec: LiveRecorder, parts_done: int) -> None:
        job = self.get(job_id)
        live = {**job["live"], "recorded": round(rec.recorded_seconds(), 1), "parts_done": parts_done,
                "parts_recorded": len(rec.parts()), "recording": not rec.ended}
        self.update(job_id, live=live)

    def _clip_part(self, job_id: str, part: Part, prev: Part | None, clock: float, opts: Options) -> None:
        job_dir = self.root / job_id
        label = f"p{part.index + 1:03d}"
        src, new_from = part.path, 0.0
        if prev is not None:
            src, new_from = _overlap_window(prev, part, job_dir / "live" / f"window_{part.index:04d}.ts")
        offset = clock - new_from
        existing = self.get(job_id)["clips"]
        exclude = [(c["abs_start"] - offset, c["abs_end"] - offset) for c in existing
                   if c["abs_end"] > offset and c["abs_start"] < offset + new_from + part.duration]
        base = list(existing)
        part_no = part.index + 1

        def upd(**f):
            fields = {}
            if f.get("log"):
                fields["log"] = f"Part {part_no}: {f['log']}"
            if f.get("message"):
                fields["message"] = f"Part {part_no} ({_hms(clock)}–{_hms(clock + part.duration)}): {f['message']}"
            if "clips" in f:
                fields["clips"] = base + f["clips"]
            if f.get("video") and not self.get(job_id).get("video"):
                fields["video"] = {**f["video"], "duration": 0}
            if fields:
                self.update(job_id, **fields)

        run_pipeline(Path(src), job_dir, opts, upd,
                     window={"label": label, "offset": offset, "new_from": new_from, "exclude": exclude})
        # free disk: the overlap window is only needed for this pass
        if src != part.path:
            Path(src).unlink(missing_ok=True)


class _ChatClipper:
    """Runs beside a live recording: reads the stream's chat, measures how busy it is, and when chat suddenly
    explodes it cuts the moment on its own, from CHAT_LEAD seconds before chat started rising to just after the
    spike, as a chat clip (captions, title, upload) next to the ones you cut by hand."""

    CHAT_LEAD = 20.0   # seconds of video before chat began to rise: the moment chat is reacting to
    TAIL = 4.0         # after the spike: the reaction itself
    MAX_LEN = 90.0

    def __init__(self, store: "JobStore", job_id: str, meta: dict, opts: Options):
        from .chat import chat_site, start_chat
        from .chat_spikes import ChatMeter

        self.store, self.job_id = store, job_id
        self.meter = ChatMeter()
        self.stop = threading.Event()
        self.offsets: list[float] = []   # wall clock minus recording clock, measured as segments complete
        self.last_rec = 0.0
        self.pending: list[dict] = []
        self.spikes: list[dict] = []
        self.started = time.time()
        self.reader: threading.Thread | None = None
        site = chat_site(meta.get("url") or "", meta)
        self.state = {"site": site, "on": bool(site), "messages": 0, "normal": 0, "series": [], "spikes": [],
                      "note": "" if site else "Chat clips work on Twitch and YouTube streams. Recording and "
                                              "cutting by hand still work."}
        self.last_note = ""

        def log(m: str) -> None:
            self.last_note = m
            store.update(job_id, log=f"Chat: {m}")
        if site:
            self.reader = start_chat(meta.get("url") or "", meta, lambda ts, user, text: self.meter.add(ts, text),
                                     log, self.stop, store.root / job_id / "work")

    def _rec_at(self, wall: float) -> float | None:
        """Where a wall-clock moment is on the recording's clock."""
        if not self.offsets:
            return None
        off = sorted(self.offsets[-15:])[len(self.offsets[-15:]) // 2]
        return wall - off

    def tick(self, recorded: float) -> dict:
        now = time.time()
        if recorded > self.last_rec + 0.5:  # a segment just closed: the recording's live edge is `recorded`
            self.offsets.append(now - recorded)
            self.last_rec = recorded
        if self.state["on"]:
            spike = self.meter.check(now)
            if spike:
                self.pending.append(spike)
            self._cut_ready(recorded)
        st = self.state
        st["messages"] = self.meter.total
        st["normal"] = round(self.meter.normal, 1)
        if self.offsets:  # the graph: messages per 5 s along the recording
            pts = self.meter.series(self.started, now)
            st["series"] = [[round(self._rec_at(w), 1), r] for w, r in pts if self._rec_at(w) >= 0][-1440:]
        st["spikes"] = self.spikes
        return st

    def _cut_ready(self, recorded: float) -> None:
        for sp in list(self.pending):
            a, b = self._rec_at(sp["start"]), self._rec_at(sp["end"])
            if a is None:
                continue
            start, end = max(0.0, a - self.CHAT_LEAD), b + self.TAIL
            start = max(start, end - self.MAX_LEN)
            done = [x["end"] for x in self.spikes if x.get("job")]
            if done and start < done[-1]:  # the last chat clip already shows this part: start where it ended
                start = done[-1] - 2.0
            if recorded < end + 1:  # wait until the recording covers the reaction
                continue
            self.pending.remove(sp)
            words = ", ".join(sp["top"][:3])
            note = (f"chat jumped to {sp['peak_rate']} messages in 5 s (normal {sp['normal']:g})"
                    + (f": {words}" if words else ""))
            entry = {"start": round(start, 1), "end": round(end, 1), "peak_rate": sp["peak_rate"],
                     "normal": sp["normal"], "top": sp["top"], "chat_start": round(a, 1)}
            try:
                child = self.store.cut_live(self.job_id, start, end, by="chat", note=note)
                entry["job"] = child["id"]
            except Exception as exc:  # noqa: BLE001 - logged; recording and manual cutting go on
                entry["error"] = str(exc)[:160]
                self.store.update(self.job_id, log=f"Chat: couldn't cut a chat clip ({str(exc)[:120]})")
            self.spikes.append(entry)

    def finish(self, recorded: float) -> dict:
        self.stop.set()
        self._cut_ready(recorded + 1e6)  # whatever is left is as covered as it will get
        self.state["on"] = False
        return self.tick(recorded)


def _overlap_window(prev: Part, cur: Part, dest: Path, overlap: float = 90.0) -> tuple[Path, float]:
    """Join the last `overlap` seconds of prev with cur (stream copy). Returns (file, seconds where cur starts)."""
    from .media import FFMPEG, probe, run

    inpoint = max(0.0, prev.duration - overlap)
    listing = dest.with_suffix(".txt")
    listing.write_text(f"file '{prev.path.name}'\ninpoint {inpoint:.3f}\nfile '{cur.path.name}'\n", encoding="utf-8")
    run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing.name,
         "-map", "0", "-c", "copy", "-f", "mpegts", dest.name], cwd=dest.parent)
    listing.unlink(missing_ok=True)
    total = probe(dest).duration
    return dest, max(0.0, total - cur.duration)


def _hms(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _error_text(exc: Exception) -> str:
    from .download import DownloadError

    return str(exc) if isinstance(exc, DownloadError) else f"{type(exc).__name__}: {exc}"
