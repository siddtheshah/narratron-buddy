"""Shared FastAPI app, singletons, flags, and utility helpers for api_server."""

import base64
import hmac
import json
import logging
import os
import re
from typing import Optional

from fastapi import Request, Response, WebSocket, HTTPException
from fastapi.staticfiles import StaticFiles

import object_registry
from api_server.dependencies import FLAGS, canvas_states, db, theater_manager

# Project root is one level above api_server/
PROJECT_ROOT = object_registry.PROJECT_ROOT

logger = logging.getLogger(__name__)
config = object_registry.config
app = object_registry.app


theaters_folder = str(theater_manager.base_dir)

static_dir = PROJECT_ROOT / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Playlists folder from config (absolute path resolution)
playlists_folder = str((PROJECT_ROOT / config.get("music", {}).get("playlists_folder", "playlists")).resolve())
os.makedirs(playlists_folder, exist_ok=True)
app.mount("/playlists", StaticFiles(directory=playlists_folder), name="playlists")

# Reference library folder (absolute path resolution)
ref_library_folder = str((PROJECT_ROOT / "reference_library").resolve())
os.makedirs(ref_library_folder, exist_ok=True)
app.mount("/reference_library", StaticFiles(directory=ref_library_folder), name="reference_library")

# Artwork used by the public join-page background carousel.
carousel_folder = str((PROJECT_ROOT / "templates" / "carousel").resolve())
app.mount("/carousel", StaticFiles(directory=carousel_folder), name="carousel")


_SAFE_PARAM_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.() \-]*$')

def _safe_path_param(value: str, label: str = "parameter") -> str:
    """Reject path components that could enable directory traversal."""
    if not value or not _SAFE_PARAM_RE.match(value) or '..' in value:
        raise HTTPException(status_code=400, detail=f"Invalid {label}.")
    return value

def get_current_user(request: Request | WebSocket, *, record_activity: bool = True) -> Optional[dict]:
    token = None
    if hasattr(request, "cookies") and request.cookies:
        token = request.cookies.get("auth_token")

    if not token and hasattr(request, "query_params") and request.query_params:
        token = request.query_params.get("auth_token") or request.query_params.get("token")

    if not token and hasattr(request, "scope"):
        headers = getattr(request, "scope", {}).get("headers", [])
        for key, value in headers:
            if key.lower() == b"cookie":
                try:
                    for part in value.decode("utf-8").split(";"):
                        name, _, cookie_value = part.partition("=")
                        if name.strip() == "auth_token":
                            token = cookie_value.strip()
                            break
                except Exception:
                    pass
                if token is not None:
                    break

    if token:
        user = db.validate_session_token(token, record_activity=record_activity)
        if user:
            return user

    return None



_CANVAS_ACCESS_COOKIE = "canvas_access"


def _canvas_access_grants(request: Request | WebSocket) -> dict[str, str]:
    """Return validly-shaped per-session join-key grants from the HttpOnly cookie."""
    if hasattr(request, "cookies"):
        encoded_grants = request.cookies.get(_CANVAS_ACCESS_COOKIE)
    else:
        encoded_grants = None
        for key, value in getattr(request, "scope", {}).get("headers", []):
            if key.lower() == b"cookie":
                for part in value.decode("utf-8").split(";"):
                    name, _, cookie_value = part.partition("=")
                    if name.strip() == _CANVAS_ACCESS_COOKIE:
                        encoded_grants = cookie_value.strip()
                        break
                if encoded_grants is not None:
                    break

    if not encoded_grants:
        return {}
    try:
        padding = "=" * (-len(encoded_grants) % 4)
        grants = json.loads(base64.urlsafe_b64decode(encoded_grants + padding).decode("utf-8"))
        return {
            theater_id: join_key
            for theater_id, join_key in grants.items()
            if isinstance(theater_id, str) and isinstance(join_key, str)
        }
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _grant_canvas_access(response: Response, request: Request, theater_id: str, join_key: str) -> None:
    """Store a verified join key outside the URL for subsequent canvas requests."""
    grants = _canvas_access_grants(request)
    grants[theater_id] = join_key
    encoded_grants = base64.urlsafe_b64encode(
        json.dumps(grants, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    response.set_cookie(
        key=_CANVAS_ACCESS_COOKIE,
        value=encoded_grants,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 12,
    )


def _valid_join_key(expected_key: str, candidate_key: Optional[str]) -> bool:
    return bool(candidate_key) and hmac.compare_digest(
        expected_key.strip().upper(), candidate_key.strip().upper()
    )


def can_access_agent_websocket(
    request: Request | WebSocket,
    deployment: dict,
    *,
    current_user: Optional[dict] = None,
    join_key: Optional[str] = None,
) -> bool:
    """Return True when the request can access the theater's agent websocket."""
    if not deployment:
        return False

    active_orator_id = deployment.get("active_orator_id")
    if current_user:
        if active_orator_id is not None:
            if current_user["id"] == active_orator_id:
                return True
        elif current_user["id"] == deployment["user_id"]:
            return True

    candidate_key = join_key or _canvas_access_grants(request).get(deployment["theater_id"])
    return _valid_join_key(deployment["join_key"], candidate_key)




def _require_canvas_access(
    request: Request, theater_id: str, join_key: Optional[str] = None
) -> dict:
    """Require ownership or a verified join-key grant before serving theater content."""
    _safe_path_param(theater_id, "theater_id")
    deployment = db.get_deployment(theater_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Active theater not found.")

    current_user = get_current_user(request)
    if can_access_agent_websocket(request, deployment, current_user=current_user, join_key=join_key):
        return deployment

    raise HTTPException(status_code=403, detail="A valid join key is required to access this theater.")
