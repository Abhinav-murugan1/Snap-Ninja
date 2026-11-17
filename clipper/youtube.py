"""YouTube channel connection (OAuth, desktop app) and resumable video uploads.

Setup: put the OAuth client file from Google Cloud ("Desktop app" type) at youtube/client_secret.json, or add it
from the web page. Signing in once stores a token in youtube/token.json; it refreshes itself after that.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent / "youtube"
CLIENT = ROOT / "client_secret.json"
TOKEN = ROOT / "token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]

_lock = threading.Lock()
_state = {"connecting": False, "error": "", "channel": None}


class YouTubeError(RuntimeError):
    pass


# --------------------------------------------------------------------------- setup / auth

def save_client_file(raw: bytes) -> None:
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise YouTubeError("That file isn't valid JSON. Download the OAuth client file again from Google Cloud.")
    if "installed" not in data:
        if "web" in data:
            raise YouTubeError("This is a “Web application” client. Create an OAuth client of type “Desktop app” "
                               "in Google Cloud and add that file instead.")
        raise YouTubeError("This doesn't look like an OAuth client file (no “installed” section).")
    ROOT.mkdir(exist_ok=True)
    CLIENT.write_text(json.dumps(data), encoding="utf-8")
    TOKEN.unlink(missing_ok=True)  # a new client means signing in again
    _state.update(channel=None, error="")


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not TOKEN.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:  # refresh token revoked or expired (7 days in Google's "Testing" mode)
            TOKEN.unlink(missing_ok=True)
            raise YouTubeError("YouTube sign-in expired. Connect the channel again.") from exc
        TOKEN.write_text(creds.to_json(), encoding="utf-8")
        return creds
    return None


def _service():
    from googleapiclient.discovery import build

    creds = _credentials()
    if not creds:
        raise YouTubeError("No YouTube channel connected yet.")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _channel_name() -> str | None:
    try:
        resp = _service().channels().list(part="snippet", mine=True).execute()
    except YouTubeError:
        return None
    items = resp.get("items") or []
    return items[0]["snippet"]["title"] if items else "(no channel on this account)"


def status() -> dict:
    connected = False
    err = _state["error"]
    if TOKEN.exists():
        try:
            connected = _credentials() is not None
        except YouTubeError as exc:
            err = str(exc)
    if connected and not _state["channel"]:
        try:
            _state["channel"] = _channel_name()
        except Exception as exc:
            err = _friendly(exc)
    return {"has_client": CLIENT.exists(), "connected": connected, "connecting": _state["connecting"],
            "channel": _state["channel"] if connected else None, "error": err}


def connect() -> None:
    """Open Google sign-in in the browser on this computer; finishes in the background."""
    if not CLIENT.exists():
        raise YouTubeError("Add the OAuth client file first.")
    with _lock:
        if _state["connecting"]:
            return
        _state.update(connecting=True, error="")

    def run():
        from google_auth_oauthlib.flow import InstalledAppFlow
        try:
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True, prompt="consent",
                                          authorization_prompt_message="",
                                          success_message="YouTube is connected. You can close this tab and go "
                                                          "back to Clipper.", timeout_seconds=300)
            TOKEN.write_text(creds.to_json(), encoding="utf-8")
            _state["channel"] = _channel_name()
        except Exception as exc:
            _state["error"] = f"Sign-in didn't finish: {_friendly(exc)}"
        finally:
            _state["connecting"] = False

    threading.Thread(target=run, daemon=True, name="youtube-connect").start()


def disconnect() -> None:
    TOKEN.unlink(missing_ok=True)
    _state.update(channel=None, error="")


# --------------------------------------------------------------------------- upload

def _friendly(exc: Exception) -> str:
    text = str(exc)
    try:
        from googleapiclient.errors import HttpError
        if isinstance(exc, HttpError):
            reason = ""
            try:
                body = json.loads(exc.content.decode("utf-8"))
                errs = body.get("error", {}).get("errors") or [{}]
                reason = errs[0].get("reason", "")
                text = body.get("error", {}).get("message", text)
            except Exception:
                pass
            if reason in ("quotaExceeded", "dailyLimitExceeded"):
                return ("This Google Cloud project's YouTube API allowance is used up. "
                        "Check its Quotas page for the current limit and reset time, then try again.")
            if reason == "uploadLimitExceeded":
                return "This channel has hit YouTube's upload limit for now. Try again later."
            if exc.resp.status in (401, 403):
                return f"YouTube refused the request: {text}"
    except ImportError:
        pass
    return text[:300]


def upload(video: Path, meta: dict, progress: Callable[[float], None]) -> dict:
    """Resumable upload. meta: title, description, tags, category_id, privacy. Returns {"id", "url"}."""
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {
            "title": meta["title"][:100],
            "description": meta.get("description", "")[:4900],
            "tags": meta.get("tags", []),
            "categoryId": str(meta.get("category_id", "24")),
        },
        "status": {
            "privacyStatus": meta.get("privacy", "public"),
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": False,  # real footage, cut and captioned
        },
    }
    try:
        service = _service()
        media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
        request = service.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        retries = 0
        while response is None:
            try:
                status_, response = request.next_chunk()
                retries = 0
            except Exception as exc:  # transient network/5xx: retry the chunk a few times
                from googleapiclient.errors import HttpError
                if isinstance(exc, HttpError) and exc.resp.status < 500:
                    raise
                retries += 1
                if retries > 5:
                    raise
                import time
                time.sleep(2 ** retries)
                continue
            if status_:
                progress(status_.progress())
        progress(1.0)
    except YouTubeError:
        raise
    except Exception as exc:
        raise YouTubeError(_friendly(exc)) from exc
    vid = response["id"]
    short = meta.get("kind") == "short"
    return {"id": vid, "url": f"https://youtube.com/shorts/{vid}" if short else f"https://youtu.be/{vid}",
            "privacy": response.get("status", {}).get("privacyStatus", body["status"]["privacyStatus"])}
