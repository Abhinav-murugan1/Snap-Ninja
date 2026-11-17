"""Record a live stream into fixed-length parts with ffmpeg, reconnecting if the stream drops."""
from __future__ import annotations

import csv
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .download import DownloadError, inspect_link
from .media import FFMPEG

LogFn = Callable[[str], None]
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Part:
    index: int
    path: Path
    duration: float


class LiveRecorder:
    """Writes part_0000.ts, part_0001.ts, ... of `part_seconds` each into out_dir.

    A part is "complete" once ffmpeg lists it in its segment list (it has started the next part, or
    exited). If ffmpeg dies while the stream is still live (network blip, expired stream URL), the
    link is looked up again and recording resumes with the next part number.
    """

    def __init__(self, url: str, meta: dict, out_dir: Path, part_seconds: float, log: LogFn):
        self.url = url
        self.meta = meta
        self.out_dir = out_dir
        self.part_seconds = part_seconds
        self.log = log
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._parts: list[Part] = []
        self._session = 0
        self.ended = False            # stream ended or recording stopped, and ffmpeg has exited
        self.end_reason = ""
        self.started_at = time.time()
        self._last_part_time = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True, name="live-recorder")

    # ------------------------------------------------------------------ public
    def start(self) -> None:
        self._thread.start()

    def stop(self, reason: str = "Recording stopped") -> None:
        if not self._stop.is_set():
            self.end_reason = reason
            self._stop.set()
            proc = self._proc
            if proc and proc.poll() is None:
                try:  # 'q' lets ffmpeg close the current part cleanly
                    proc.stdin.write(b"q")
                    proc.stdin.flush()
                except (OSError, ValueError):
                    proc.terminate()
                threading.Thread(target=self._kill_later, args=(proc,), daemon=True).start()

    @staticmethod
    def _kill_later(proc: subprocess.Popen) -> None:
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()

    def parts(self) -> list[Part]:
        self._collect()
        with self._lock:
            return list(self._parts)

    def recorded_seconds(self) -> float:
        done = sum(p.duration for p in self.parts())
        current = self._current_part_path()
        if current is not None and not self.ended:
            # rough: time since the last complete part
            done += min(self.part_seconds, max(0.0, time.time() - self._last_part_time))
        return done

    # ------------------------------------------------------------------ internals
    def _list_file(self, session: int) -> Path:
        return self.out_dir / f"parts_{session:02d}.csv"

    def _collect(self) -> None:
        found: list[Part] = []
        for s in range(self._session + 1):
            lf = self._list_file(s)
            if not lf.exists():
                continue
            try:
                rows = list(csv.reader(lf.read_text(encoding="utf-8", errors="replace").splitlines()))
            except OSError:
                continue
            for row in rows:
                if len(row) < 3:
                    continue
                path = self.out_dir / row[0]
                try:
                    dur = float(row[2]) - float(row[1])
                except ValueError:
                    continue
                if path.exists() and dur > 1.0:
                    idx = int(path.stem.split("_")[-1])
                    found.append(Part(idx, path, dur))
        found = list({p.index: p for p in found}.values())  # a part can appear in two session lists
        found.sort(key=lambda p: p.index)
        with self._lock:
            if len(found) > len(self._parts):
                self._last_part_time = time.time()
            self._parts = found

    def _current_part_path(self) -> Path | None:
        files = sorted(self.out_dir.glob("part_*.ts"))
        return files[-1] if files else None

    def _next_index(self) -> int:
        files = sorted(self.out_dir.glob("part_*.ts"))
        return int(files[-1].stem.split("_")[-1]) + 1 if files else 0

    def _command(self, meta: dict, start_number: int) -> list[str]:
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
        headers = "".join(f"{k}: {v}\r\n" for k, v in (meta.get("headers") or {}).items())
        for u in meta["stream_urls"]:
            if headers:
                cmd += ["-headers", headers]
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_on_network_error", "1",
                    "-reconnect_delay_max", "10", "-i", u]
        if len(meta["stream_urls"]) > 1:
            cmd += ["-map", "0:v:0", "-map", "1:a:0"]
        else:
            cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
        cmd += ["-c", "copy", "-f", "segment", "-segment_time", str(int(self.part_seconds)),
                "-segment_format", "mpegts", "-reset_timestamps", "1",
                "-segment_start_number", str(start_number),
                "-segment_list", self._list_file(self._session).name, "-segment_list_type", "csv",
                "part_%04d.ts"]
        return cmd

    def _run(self) -> None:
        meta = self.meta
        self._last_part_time = time.time()
        failures = 0
        while not self._stop.is_set():
            cmd = self._command(meta, self._next_index())
            err_path = self.out_dir / f"ffmpeg_{self._session:02d}.log"
            with open(err_path, "wb") as err_file:
                self._proc = subprocess.Popen(cmd, cwd=str(self.out_dir), stdin=subprocess.PIPE,
                                              stdout=subprocess.DEVNULL, stderr=err_file,
                                              creationflags=_NO_WINDOW)
                started = time.time()
                self._proc.wait()
            err = err_path.read_bytes()
            self._collect()
            if self._stop.is_set():
                break
            # ffmpeg exited by itself: stream ended, or the connection/URL went bad
            failures = failures + 1 if time.time() - started < 60 else 1
            try:
                fresh = inspect_link(self.url)
            except DownloadError as exc:
                fresh = None
                self.log(f"Couldn't re-open the stream: {exc}")
            if not fresh or not fresh.get("is_live"):
                self.end_reason = "The stream ended"
                break
            if failures >= 5:
                self.end_reason = "The stream kept disconnecting, so recording stopped"
                break
            tail = (err or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
            self.log(f"Stream connection dropped ({tail[0][:120]}); reconnecting")
            meta = fresh
            self._session += 1
            time.sleep(min(10, 2 * failures))
        self._collect()
        self.ended = True


class LiveDVR(LiveRecorder):
    """Records the stream as a growing HLS playlist (index.m3u8 + 4 s seg_NNNNN.ts) that a browser can play and
    scrub back through while recording continues, so the user can mark clips by hand. Positions are seconds
    from the start of the recording (the sum of the segment lengths), the same clock the player shows."""

    SEGMENT = 4

    def __init__(self, url: str, meta: dict, out_dir: Path, log: LogFn):
        super().__init__(url, meta, out_dir, self.SEGMENT, log)

    def _command(self, meta: dict, start_number: int) -> list[str]:
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
        headers = "".join(f"{k}: {v}\r\n" for k, v in (meta.get("headers") or {}).items())
        for u in meta["stream_urls"]:
            if headers:
                cmd += ["-headers", headers]
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_on_network_error", "1",
                    "-reconnect_delay_max", "10", "-i", u]
        if len(meta["stream_urls"]) > 1:
            cmd += ["-map", "0:v:0", "-map", "1:a:0"]
        else:
            cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
        flags = "append_list+independent_segments" + ("+discont_start" if start_number else "")
        cmd += ["-c", "copy", "-f", "hls", "-hls_time", str(self.SEGMENT), "-hls_list_size", "0",
                "-hls_playlist_type", "event", "-hls_flags", flags, "-start_number", str(start_number),
                "-hls_segment_filename", "seg_%05d.ts", "index.m3u8"]
        return cmd

    def _next_index(self) -> int:
        files = sorted(self.out_dir.glob("seg_*.ts"))
        return int(files[-1].stem.split("_")[-1]) + 1 if files else 0

    def _current_part_path(self) -> Path | None:
        files = sorted(self.out_dir.glob("seg_*.ts"))
        return files[-1] if files else None

    def _collect(self) -> None:
        segs = playlist_segments(self.out_dir / "index.m3u8")
        with self._lock:
            if len(segs) > len(self._parts):
                self._last_part_time = time.time()
            self._parts = [Part(i, p, d) for i, (p, _, d) in enumerate(segs)]

    def recorded_seconds(self) -> float:
        return sum(p.duration for p in self.parts())


def playlist_segments(m3u8: Path) -> list[tuple[Path, float, float]]:
    """(file, start, duration) of every segment in an HLS playlist, start = seconds from its first segment."""
    try:
        text = m3u8.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out: list[tuple[Path, float, float]] = []
    t, dur = 0.0, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            try:
                dur = float(line[8:].split(",")[0])
            except ValueError:
                dur = None
        elif line and not line.startswith("#") and dur is not None:
            path = m3u8.parent / line
            if path.exists():
                out.append((path, t, dur))
            t += dur
            dur = None
    return out


def cut_recording(m3u8: Path, start: float, end: float, dest: Path) -> Path:
    """Cut [start, end] (seconds on the recording's clock) out of the HLS recording into an MP4 (re-encoded so
    the cut is frame-exact, not on a keyframe)."""
    from .media import run, video_encoder

    segs = [s for s in playlist_segments(m3u8) if s[1] + s[2] > start and s[1] < end]
    if not segs:
        raise ValueError("That part of the stream hasn't been recorded.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    listing = dest.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p, _, _ in segs), encoding="utf-8")
    offset = max(0.0, start - segs[0][1])
    try:
        run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-ss", f"{offset:.3f}", "-t", f"{end - start:.3f}", "-map", "0:v:0", "-map", "0:a:0?",
             *video_encoder(), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dest)])
    finally:
        listing.unlink(missing_ok=True)
    return dest
