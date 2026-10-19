"""Lightweight remote for controlling an existing theater from another device."""

from fastapi import HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from api_server.dependencies import live_agent_manager
from api_server.shared import (
    PROJECT_ROOT,
    app,
    canvas_states,
    can_control_agent_websocket,
    db,
    get_current_user,
    _require_canvas_access,
)


class RemoteStatus(BaseModel):
    theater_id: str
    agent_running: bool
    pinned: bool
    music_pinned: bool
    viewer_collab_enabled: bool


@app.get("/remote", response_class=HTMLResponse)
def read_remote() -> HTMLResponse:
    """Serve a public shell; all theater reads and commands require authorization."""
    return HTMLResponse(
        (PROJECT_ROOT / "templates" / "remote.html").read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@app.get("/api/theaters/{theater_id}/remote", response_model=RemoteStatus)
def get_remote_status(theater_id: str, request: Request, response: Response) -> RemoteStatus:
    """Check current baton ownership on every poll, including after a handoff."""
    response.headers["Cache-Control"] = "no-store"
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in on this device to use the remote.")
    _require_canvas_access(request, theater_id)
    deployment = db.get_deployment(theater_id)
    if not can_control_agent_websocket(deployment, current_user=user):
        raise HTTPException(status_code=403, detail="Only the active orator can use this remote.")
    state = canvas_states.get(theater_id)
    session = live_agent_manager.get_session(theater_id)
    return RemoteStatus(
        theater_id=theater_id,
        agent_running=bool(session and session.is_alive),
        pinned=state.visual.pinned,
        music_pinned=state.audio.pinned,
        viewer_collab_enabled=state.ui.viewer_collab_enabled,
    )
