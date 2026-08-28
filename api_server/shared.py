"""Shared FastAPI app, singletons, flags, and utility helpers for api_server."""

import base64
import asyncio
import hmac
import json
import logging
import os
import re
from typing import Optional

from fastapi import Request, Response, WebSocket, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import object_registry
from api_server.dependencies import FLAGS, canvas_states, db, theater_manager  # noqa: F401
from utils.auth_cache import auth_session_cache
from api_server.theater_access_cache import theater_access_cache
from storage.database import DatabaseConnectionTimeout

# Project root is one level above api_server/
PROJECT_ROOT = object_registry.PROJECT_ROOT

logger = logging.getLogger(__name__)
config = object_registry.config
app = object_registry.app


@app.exception_handler(DatabaseConnectionTimeout)
async def database_connection_timeout_handler(request: Request, exc: DatabaseConnectionTimeout):
    """Return a bounded failure instead of exhausting request workers."""
    logger.warning("Database connection checkout timed out for %s.", request.url.path)
    return JSONResponse(
        status_code=503,
        content={"detail": "Database service is temporarily unavailable. Please retry."},
    )


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

def _auth_token_from_request(request: Request | WebSocket) -> Optional[str]:
    """Extract an auth token without doing I/O."""
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
    return token


def get_current_user(request: Request | WebSocket, *, record_activity: bool = True) -> Optional[dict]:
    # Several helpers may authenticate the same request.  Keep this result on
    # the request object so only the first needs a cache/database lookup.
    request_cache = getattr(request, "state", request)
    if hasattr(request_cache, "_narratron_current_user"):
        cached = request_cache._narratron_current_user
        return dict(cached) if cached else None

    token = _auth_token_from_request(request)

    if token:
        user = auth_session_cache.get_or_validate(
            token,
            lambda: db.validate_session_token(token, record_activity=record_activity),
        )
        if user:
            setattr(request_cache, "_narratron_current_user", dict(user))
            return user

    setattr(request_cache, "_narratron_current_user", None)
    return None


async def get_current_user_async(
    request: Request | WebSocket, *, record_activity: bool = True
) -> Optional[dict]:
    """Resolve a user without blocking an async request on a cache miss."""
    request_cache = getattr(request, "state", request)
    if hasattr(request_cache, "_narratron_current_user"):
        cached = request_cache._narratron_current_user
        return dict(cached) if cached else None

    token = _auth_token_from_request(request)
    if token:
        user = await asyncio.to_thread(
            auth_session_cache.get_or_validate,
            token,
            lambda: db.validate_session_token(token, record_activity=record_activity),
        )
        if user:
            setattr(request_cache, "_narratron_current_user", dict(user))
            return user

    setattr(request_cache, "_narratron_current_user", None)
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
    """Return True when the request may access a theater's protected canvas data.

    Canvas access belongs to the theater owner, the active baton holder, or
    someone with a valid join key.  Transferring the microphone must not lock
    the owner out of their own theater.
    """
    if not deployment:
        return False

    if current_user:
        current_user_id = current_user.get("id")
        if current_user_id in {
            deployment.get("user_id"),
            deployment.get("active_orator_id"),
        }:
            return True

    candidate_key = join_key or _canvas_access_grants(request).get(deployment["theater_id"])
    return _valid_join_key(deployment["join_key"], candidate_key)


def can_control_agent_websocket(
    deployment: dict,
    *,
    current_user: Optional[dict] = None,
) -> bool:
    """Return whether the authenticated holder of the baton may control the agent.

    A join key grants access to the canvas, but never permission to send live
    microphone input to the agent.  Until a baton transfer is accepted, the
    theater owner is the active orator.
    """
    if not deployment or not current_user:
        return False
    active_orator_id = deployment.get("active_orator_id") or deployment.get("user_id")
    return current_user.get("id") == active_orator_id




def _require_canvas_access(
    request: Request, theater_id: str, join_key: Optional[str] = None
) -> dict:
    """Require ownership or a verified join-key grant before serving theater content."""
    _safe_path_param(theater_id, "theater_id")
    current_user = get_current_user(request)
    candidate_key = join_key or _canvas_access_grants(request).get(theater_id)
    principal = theater_access_cache.principal_key(
        user_id=current_user.get("id") if current_user else None,
        join_key=candidate_key,
    )
    request_cache = getattr(request, "state", request)
    memoized = getattr(request_cache, "_narratron_theater_access", {})
    cache_key = (theater_id, principal)
    if cache_key in memoized:
        deployment, allowed = memoized[cache_key]
    else:
        def resolve() -> tuple[Optional[dict], bool]:
            deployment = db.get_deployment(theater_id)
            return (
                deployment,
                bool(deployment) and can_access_agent_websocket(
                    request,
                    deployment,
                    current_user=current_user,
                    join_key=candidate_key,
                ),
            )

        deployment, allowed = theater_access_cache.get_or_resolve(theater_id, principal, resolve)
        memoized[cache_key] = (deployment, allowed)
        setattr(request_cache, "_narratron_theater_access", memoized)

    if not deployment:
        raise HTTPException(status_code=404, detail="Active theater not found.")
    if allowed:
        return deployment

    raise HTTPException(status_code=403, detail="A valid join key is required to access this theater.")


async def _require_canvas_access_async(
    request: Request | WebSocket, theater_id: str, join_key: Optional[str] = None
) -> dict:
    """Require canvas access without blocking the event loop on a cache miss."""
    _safe_path_param(theater_id, "theater_id")
    current_user = await get_current_user_async(request)
    candidate_key = join_key or _canvas_access_grants(request).get(theater_id)
    principal = theater_access_cache.principal_key(
        user_id=current_user.get("id") if current_user else None,
        join_key=candidate_key,
    )
    request_cache = getattr(request, "state", request)
    memoized = getattr(request_cache, "_narratron_theater_access", {})
    cache_key = (theater_id, principal)
    if cache_key in memoized:
        deployment, allowed = memoized[cache_key]
    else:
        def resolve() -> tuple[Optional[dict], bool]:
            deployment = db.get_deployment(theater_id)
            return (
                deployment,
                bool(deployment) and can_access_agent_websocket(
                    request,
                    deployment,
                    current_user=current_user,
                    join_key=candidate_key,
                ),
            )

        deployment, allowed = await asyncio.to_thread(
            theater_access_cache.get_or_resolve, theater_id, principal, resolve
        )
        memoized[cache_key] = (deployment, allowed)
        setattr(request_cache, "_narratron_theater_access", memoized)

    if not deployment:
        raise HTTPException(status_code=404, detail="Active theater not found.")
    if allowed:
        return deployment
    raise HTTPException(status_code=403, detail="A valid join key is required to access this theater.")
