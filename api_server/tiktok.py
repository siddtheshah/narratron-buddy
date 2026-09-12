"""TikTok OAuth and direct-to-TikTok draft upload endpoints."""

import json
import logging
import os
import secrets
import shutil
import subprocess
import tempfile
import time
from typing import Dict
from urllib.parse import urlencode, urlsplit
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import HTTPError

from fastapi import File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from api_server.shared import app, get_current_user


_PENDING: Dict[str, tuple[int, float]] = {}
_TOKENS: Dict[int, dict] = {}
_AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
_UPLOAD_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
_LAST_PUBLISH_ID: Dict[int, str] = {}
logger = logging.getLogger(__name__)


class DraftUploadRequest(BaseModel):
    video_size: int = Field(gt=0)
    mime_type: str = Field(pattern=r"^video/(mp4|webm|quicktime)$")


def _config() -> tuple[str, str, str]:
    client_key = os.getenv("TIKTOK_CLIENT", "").strip()
    client_secret = os.getenv("TIKTOK_CLIENT_SECRET", "").strip()
    base_url = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    parsed = urlsplit(base_url)
    if not client_key or not client_secret or parsed.scheme != "https" or not parsed.netloc:
        raise HTTPException(status_code=503, detail="TikTok draft export is not configured.")
    return client_key, client_secret, f"{base_url}/api/tiktok/callback"


def _post_form(url: str, values: dict) -> dict:
    payload = urlencode(values).encode("utf-8")
    request = UrlRequest(url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(request, timeout=20) as response:  # nosec B310 - fixed TikTok endpoints
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        logger.warning("TikTok token request failed: %s", payload)
        raise HTTPException(status_code=502, detail="TikTok rejected the authorization request.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="TikTok could not complete the request.") from exc


@app.get("/api/tiktok/connect")
def connect_tiktok(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to Narratron before connecting TikTok.")
    client_key, _, callback = _config()
    state = secrets.token_urlsafe(32)
    _PENDING[state] = (user["id"], time.time() + 600)
    query = urlencode({"client_key": client_key, "response_type": "code", "scope": "video.upload", "redirect_uri": callback, "state": state})
    return RedirectResponse(f"{_AUTHORIZE_URL}?{query}")


@app.get("/api/tiktok/callback", response_class=HTMLResponse)
def tiktok_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    user = get_current_user(request)
    pending = _PENDING.pop(state, None)
    if error or not user or not pending or pending[0] != user["id"] or pending[1] < time.time() or not code:
        return HTMLResponse("<script>window.opener?.postMessage({type:'narratron-tiktok-error'}, window.location.origin);window.close()</script>", status_code=400)
    client_key, client_secret, callback = _config()
    token = _post_form(_TOKEN_URL, {"client_key": client_key, "client_secret": client_secret, "code": code, "grant_type": "authorization_code", "redirect_uri": callback})
    if not token.get("access_token") or "video.upload" not in token.get("scope", ""):
        return HTMLResponse("<script>window.opener?.postMessage({type:'narratron-tiktok-error'}, window.location.origin);window.close()</script>", status_code=400)
    _TOKENS[user["id"]] = {"access_token": token["access_token"], "expires_at": time.time() + int(token.get("expires_in", 0))}
    return HTMLResponse("<script>window.opener?.postMessage({type:'narratron-tiktok-connected'}, window.location.origin);window.close()</script>")


@app.post("/api/tiktok/draft-upload")
def initialize_draft_upload(payload: DraftUploadRequest, request: Request):
    user = get_current_user(request)
    token = _TOKENS.get(user["id"]) if user else None
    if not token or token["expires_at"] <= time.time():
        raise HTTPException(status_code=401, detail="Connect TikTok to send this clip as a draft.")
    source = {"source": "FILE_UPLOAD", "video_size": payload.video_size, "chunk_size": payload.video_size, "total_chunk_count": 1}
    body = json.dumps({"source_info": source}).encode("utf-8")
    upstream = UrlRequest(_UPLOAD_INIT_URL, data=body, headers={"Authorization": f"Bearer {token['access_token']}", "Content-Type": "application/json"})
    try:
        with urlopen(upstream, timeout=20) as response:  # nosec B310 - fixed TikTok endpoint
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raw_error = exc.read().decode("utf-8", errors="replace")
        logger.warning("TikTok draft initialization failed: %s", raw_error)
        try:
            provider_error = json.loads(raw_error).get("error", {})
            reason = provider_error.get("code") or provider_error.get("message")
        except json.JSONDecodeError:
            reason = None
        raise HTTPException(status_code=502, detail=f"TikTok declined the draft upload{f': {reason}' if reason else ''}.") from exc
    data = result.get("data", {})
    provider_error = result.get("error", {})
    if provider_error.get("code") not in {None, "ok"} or not data.get("upload_url"):
        reason = provider_error.get("code") or provider_error.get("message")
        logger.warning("TikTok draft initialization declined: %s", provider_error)
        raise HTTPException(status_code=502, detail=f"TikTok declined the draft upload{f': {reason}' if reason else ''}.")
    _LAST_PUBLISH_ID[user["id"]] = data["publish_id"]
    return {"upload_url": data["upload_url"], "publish_id": data["publish_id"]}


@app.post("/api/tiktok/draft-upload-transcoded")
def upload_transcoded_draft(request: Request, clip: UploadFile = File(...)):
    """Convert a browser WebM privately, upload its MP4 to TikTok, then delete it."""
    user = get_current_user(request)
    token = _TOKENS.get(user["id"]) if user else None
    if not token or token["expires_at"] <= time.time():
        raise HTTPException(status_code=401, detail="Connect TikTok to send this clip as a draft.")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise HTTPException(status_code=503, detail="MP4 conversion is unavailable on this server.")
    with tempfile.TemporaryDirectory(prefix="narratron-tiktok-") as working_dir:
        source = os.path.join(working_dir, "clip.webm")
        converted = os.path.join(working_dir, "clip.mp4")
        with open(source, "wb") as target:
            while chunk := clip.file.read(1024 * 1024):
                target.write(chunk)
        try:
            subprocess.run([ffmpeg, "-y", "-i", source, "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-movflags", "+faststart", converted], check=True, capture_output=True, timeout=120)
        except subprocess.CalledProcessError as exc:
            raw_stderr = exc.stderr.decode("utf-8", errors="replace").strip()
            diagnostic = raw_stderr.splitlines()
            detail = " | ".join(diagnostic[-8:]) if diagnostic else "FFmpeg returned an unknown conversion error."
            logger.warning("TikTok MP4 conversion failed: %s\nFull stderr: %s", detail, raw_stderr)
            raise HTTPException(status_code=422, detail=f"Could not convert this clip: {detail}") from exc
        except subprocess.TimeoutExpired as exc:
            logger.warning("TikTok MP4 conversion timed out.")
            raise HTTPException(status_code=422, detail="MP4 conversion timed out.") from exc
        size = os.path.getsize(converted)
        body = json.dumps({"source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}}).encode("utf-8")
        upstream = UrlRequest(_UPLOAD_INIT_URL, data=body, headers={"Authorization": f"Bearer {token['access_token']}", "Content-Type": "application/json"})
        try:
            with urlopen(upstream, timeout=20) as response:  # nosec B310 - fixed TikTok endpoint
                init = json.loads(response.read().decode("utf-8"))
            data = init["data"]
            with open(converted, "rb") as media:
                put = UrlRequest(data["upload_url"], data=media.read(), method="PUT", headers={"Content-Type": "video/mp4", "Content-Length": str(size), "Content-Range": f"bytes 0-{size - 1}/{size}"})
                with urlopen(put, timeout=120):  # nosec B310 - one-time URL supplied by TikTok
                    pass
        except Exception as exc:
            raise HTTPException(status_code=502, detail="TikTok could not receive the converted MP4.") from exc
    _LAST_PUBLISH_ID[user["id"]] = data["publish_id"]
    return {"publish_id": data["publish_id"], "transcoded": True}


@app.get("/api/tiktok/draft-status")
def draft_status(request: Request):
    """Return TikTok's state for the current user's most recent draft handoff."""
    user = get_current_user(request)
    token = _TOKENS.get(user["id"]) if user else None
    publish_id = _LAST_PUBLISH_ID.get(user["id"]) if user else None
    if not token or not publish_id:
        raise HTTPException(status_code=404, detail="No recent TikTok draft handoff is available.")
    body = json.dumps({"publish_id": publish_id}).encode("utf-8")
    upstream = UrlRequest(_STATUS_URL, data=body, headers={"Authorization": f"Bearer {token['access_token']}", "Content-Type": "application/json"})
    try:
        with urlopen(upstream, timeout=20) as response:  # nosec B310 - fixed TikTok endpoint
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail="TikTok could not fetch draft status.") from exc
    if result.get("error", {}).get("code") not in {None, "ok"}:
        raise HTTPException(status_code=502, detail="TikTok declined the draft status check.")
    return {"publish_id": publish_id, **result.get("data", {})}
