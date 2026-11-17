"""Snap Ninja as a desktop app: its own window (Windows' built-in Edge WebView2), its own icon, no browser and no
console. The same server as run.bat runs inside the app, so every feature works the same.

Start it from the Start menu or the desktop shortcut (make them with make_shortcuts.bat), or:
    .venv\\Scripts\\pythonw.exe desktop.py
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path
from clipper.paths import configure_runtime, data_root, resource_root

ROOT = data_root()
ICON = resource_root() / "static" / "snap-ninja-v1.ico"
TITLE = "Snap Ninja"
PREFERRED_PORT = 7860

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32


# ------------------------------------------------------------------ plumbing
def _log_to_file() -> None:
    """No console in the app: everything that would be printed goes to logs/clipper.log."""
    logs = ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "clipper.log"
    if path.exists() and path.stat().st_size > 5_000_000:
        path.replace(logs / "clipper.old.log")
    f = open(path, "a", encoding="utf-8", buffering=1)
    f.write(f"\n===== Snap Ninja started {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    sys.stdout = sys.stderr = f
    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # no line per request


def _already_running() -> bool:
    """One Snap Ninja at a time (two would run the same jobs twice). If it's open, bring its window forward."""
    global _mutex
    _mutex = _kernel32.CreateMutexW(None, False, "Local\\ClipperDesktopApp")
    if _kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
        return False
    hwnd = _user32.FindWindowW(None, TITLE)
    if hwnd:
        _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        _user32.SetForegroundWindow(hwnd)
    return True


def _clipper_at(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/desktop-instance", timeout=2) as r:
            data = json.load(r)
            return (isinstance(data, dict) and data.get("app") == TITLE
                    and Path(data.get("data_root", "")).resolve() == ROOT.resolve())
    except (OSError, ValueError, TypeError):
        return False


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ------------------------------------------------------------------ app
def main() -> None:
    configure_runtime()
    if _already_running():
        return
    _log_to_file()
    from clipper.winproc import children_die_with_me

    children_die_with_me()  # ffmpeg stops when the app closes
    try:  # its own taskbar button with Snap Ninja's icon, not Python's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SnapNinja.Desktop")
    except (AttributeError, OSError):
        pass

    import webview

    server = store = None
    if _port_free(PREFERRED_PORT):
        port = PREFERRED_PORT
    elif _clipper_at(PREFERRED_PORT):
        port = None  # Snap Ninja is already running from run.bat: show that one, don't start a second
    else:
        port = _free_port()
    if port:
        from werkzeug.serving import make_server

        import app as clipper_app  # loads the jobs and starts the workers

        store = clipper_app.store
        server = make_server("127.0.0.1", port, clipper_app.app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True, name="clipper-server").start()
    url = f"http://127.0.0.1:{port or PREFERRED_PORT}/"

    window = webview.create_window(TITLE, url, width=1320, height=900, min_size=(420, 600),
                                   background_color="#090c0b", text_select=True)

    def busy() -> list[str]:
        if store is None:
            return []
        out = []
        for j in store.list():
            job = store.get(j["id"]) or {}
            live = job.get("live") or {}
            if j["status"] in ("queued", "running"):
                what = "recording" if live.get("recording") else ("waiting" if j["status"] == "queued" else "working")
                out.append(f"• {j['name'][:70]} ({what})")
            elif any(c.get("editing") for c in job.get("clips") or []):
                out.append(f"• {j['name'][:70]} (re-editing a clip)")
        return out

    def on_closing():
        jobs = busy()
        if not jobs:
            return True
        msg = ("Snap Ninja is still working on:\n\n" + "\n".join(jobs[:6]) + ("\n…" if len(jobs) > 6 else "")
               + "\n\nIf you close it now, this work stops. Clips that are already finished are kept, and a "
                 "live recording keeps what it has recorded. Close Snap Ninja anyway?")
        hwnd = _user32.FindWindowW(None, TITLE)
        answer = _user32.MessageBoxW(hwnd, msg, TITLE, 0x4 | 0x30 | 0x100 | 0x10000)  # yes/no, warning, No first
        return answer == 6  # IDYES

    window.events.closing += on_closing
    webview.settings["ALLOW_DOWNLOADS"] = True              # "Download short" opens a Save dialog
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True  # YouTube links open in your browser
    webview.start(gui="edgechromium", private_mode=False, storage_path=str(ROOT / ".webview"),
                  icon=str(ICON) if ICON.exists() else None)

    # the window is closed: end live recordings cleanly so their recording stays playable
    if store is not None:
        lives = list(store.stops)
        for job_id in lives:
            store.request_stop(job_id)
        if lives:
            time.sleep(4)
    if server is not None:
        server.shutdown()
    os._exit(0)  # worker threads end with the app; the job object stops any helper still running


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    if len(sys.argv) == 3 and sys.argv[1] == "--pick-output":
        from tools.pick_output import main as pick_folder
        sys.argv = [sys.argv[0], sys.argv[2]]
        pick_folder()
    elif len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from clipper.packaged_check import run
        sys.exit(run(Path(sys.argv[2])))
    else:
        try:
            main()
        except Exception:
            import traceback
            ROOT.mkdir(parents=True, exist_ok=True)
            (ROOT / "startup-error.log").write_text(traceback.format_exc(), encoding="utf-8")
            _user32.MessageBoxW(None, "Snap Ninja could not start. Details were saved to:\n" +
                               str(ROOT / "startup-error.log"), TITLE, 0x10)
            sys.exit(1)
