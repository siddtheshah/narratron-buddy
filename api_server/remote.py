"""Lightweight remote for controlling an existing theater from another device."""

from fastapi import HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
import jinja2
from pydantic import BaseModel

from api_server.dependencies import live_agent_manager
from api_server.shared import (
    PROJECT_ROOT,
    app,
    canvas_states,
    db,
    get_current_user,
    is_contributor,
    _extract_candidate_join_key,
    _grant_canvas_access,
    _require_canvas_access,
    _valid_join_key,
)

_REMOTE_BODY_TEMPLATE = jinja2.Environment(autoescape=True).from_string(
    "<body"
    '{% if is_owner %} data-is-owner="true"{% endif %}'
    '{% if pure_viewer %} data-pure-viewer="true"{% elif owner_idle %} data-owner-idle="true"{% endif %}'
    '{% if join_key %} data-join-key="{{ join_key }}"{% endif %}'
    ">"
)


class RemoteStatus(BaseModel):
    theater_id: str
    agent_running: bool
    pinned: bool
    music_pinned: bool
    viewer_collab_enabled: bool
    preview_url: str | None = None
    is_contributor: bool = False
    is_owner: bool = False
    is_orator: bool = False


@app.get("/remote", response_class=HTMLResponse)
def read_remote(
    request: Request,
    theater_id: str = Query(..., min_length=1, pattern=r"^[a-zA-Z0-9_-]+$"),
    join_key: str | None = None,
) -> HTMLResponse:
    """Require a theater and login before serving the remote controls."""
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    user = get_current_user(request)
    if not user:
        return HTMLResponse(
            (PROJECT_ROOT / "templates" / "remote_login.html").read_text(encoding="utf-8"),
            status_code=401, headers=headers,
        )
    deployment = _require_canvas_access(request, theater_id, join_key=join_key)
    content = (PROJECT_ROOT / "templates" / "remote.html").read_text(encoding="utf-8")
    user_id = user.get("id")
    is_owner = bool(deployment and user_id and user_id == deployment.get("user_id"))
    active_orator_id = deployment.get("active_orator_id") if deployment else None
    if active_orator_id is None and deployment:
        active_orator_id = deployment.get("user_id")
    is_orator = bool(user_id and user_id == active_orator_id)

    raw_authoritative_key = deployment.get("join_key") if deployment else None
    authoritative_key = raw_authoritative_key.strip() if raw_authoritative_key else ""
    candidate_key = _extract_candidate_join_key(request, theater_id, join_key=join_key)
    has_valid_join_key = bool(
        authoritative_key and candidate_key and _valid_join_key(authoritative_key, candidate_key)
    )
    resolved_key = (
        authoritative_key
        if authoritative_key and (is_owner or is_orator or has_valid_join_key)
        else ""
    )

    body_tag = _REMOTE_BODY_TEMPLATE.render(
        is_owner=is_owner,
        pure_viewer=(not is_owner and not is_orator),
        owner_idle=(is_owner and not is_orator),
        join_key=resolved_key,
    )
    content = content.replace("<body>", body_tag, 1)

    response = HTMLResponse(content, headers=headers)
    if has_valid_join_key:
        _grant_canvas_access(response, request, theater_id, authoritative_key)
    return response


@app.get("/api/theaters/{theater_id}/remote", response_model=RemoteStatus)
def get_remote_status(theater_id: str, request: Request, response: Response) -> RemoteStatus:
    """Check participant permissions on every poll."""
    response.headers["Cache-Control"] = "no-store"
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in on this device to use the remote.")
    _require_canvas_access(request, theater_id)
    deployment = db.get_deployment(theater_id)
    state = canvas_states.get(theater_id)
    session = live_agent_manager.get_session(theater_id)
    user_id = user.get("id")
    is_owner = bool(deployment and user_id and user_id == deployment.get("user_id"))
    active_orator_id = deployment.get("active_orator_id") if deployment else None
    if active_orator_id is None and deployment:
        active_orator_id = deployment.get("user_id")
    is_orator = bool(user_id and user_id == active_orator_id)

    return RemoteStatus(
        theater_id=theater_id,
        agent_running=bool(session and session.is_alive),
        pinned=state.visual.pinned,
        music_pinned=state.audio.pinned,
        viewer_collab_enabled=state.ui.viewer_collab_enabled,
        preview_url=state.visual.preview_image_url(),
        is_contributor=is_contributor(deployment, current_user=user),
        is_owner=is_owner,
        is_orator=is_orator,
    )
