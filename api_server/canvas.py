"""Canvas WebSocket, chat, orator control, and stats API endpoints."""

from typing import Optional

from fastapi import Request, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel

from api_server.shared import (
    app,
    config,
    canvas_states,
    db,
    theater_manager,
    get_current_user,
    _require_canvas_access,
)


class ChatMessage(BaseModel):
    author: str
    text: str


class SuggestionVote(BaseModel):
    voter: str
    target_author: str


class SuggestionWithdrawal(BaseModel):
    author: str


class ViewerCollabRequest(BaseModel):
    enabled: bool


# ========================================
# Canvas & WebSocket Endpoints
# ========================================

@app.websocket("/ws/doodle")
async def websocket_endpoint(websocket: WebSocket, theater_id: Optional[str] = None):
    if theater_id:
        try:
            _require_canvas_access(websocket, theater_id)
        except HTTPException:
            await websocket.close(code=1008)
            return
    await websocket.accept()
    websocket.state.theater_id = theater_id
    current_user = get_current_user(websocket)
    cs = await canvas_states.connect_doodle_websocket(websocket, theater_id, user=current_user)
    
    if theater_id:
        baton_st = db.get_theater_baton_state(theater_id)
        if baton_st:
            await canvas_states.broadcast_baton_update(theater_id, baton_st)

    try:
        while True:
            data = await websocket.receive_json()
            await canvas_states.apply_doodle_message(cs, data, sender=websocket)
    except WebSocketDisconnect:
        cs.unregister_websocket(websocket)
        if theater_id:
            baton_st = db.get_theater_baton_state(theater_id)
            if baton_st:
                await canvas_states.broadcast_baton_update(theater_id, baton_st)


@app.api_route("/api/orator/toggle_mic", methods=["GET", "POST"])
async def trigger_orator_mic_toggle(request: Request, theater_id: Optional[str] = None):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to control Orator microphone.")
    
    if theater_id:
        dep = db.get_deployment(theater_id)
        if dep and dep["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Permission denied. Only the theater owner can control the Orator microphone.")

    count = await canvas_states.toggle_microphone(theater_id)
    return {"status": "ok", "broadcasted_to": count}


@app.get("/api/orator/config")
def get_orator_config():
    return config.get("orator", {
        "hotkey": "<ctrl>+<shift>+[",
        "server_url": "http://127.0.0.1:8000/api/orator/toggle_mic"
    })

@app.get("/api/latest")
def get_latest_image(request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)
        theater_dir = theater_manager.theater(theater_id).directory()
        if not theater_dir.exists():
            db.reconstruct_theater_from_db(theater_id, theater_dir)
    return canvas_states.latest_state(theater_id)

@app.get("/api/chat")
def get_chat(request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)
    return canvas_states.chat_messages(theater_id)

@app.post("/api/chat")
def post_chat(msg: ChatMessage, request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)

    command_parts = msg.text.strip().split(maxsplit=1)
    if command_parts and command_parts[0].lower() == "/suggest":
        suggestion_text = command_parts[1] if len(command_parts) > 1 else ""
        if not suggestion_text:
            raise HTTPException(status_code=400, detail="A suggestion must include text after /suggest.")
        try:
            suggestion = canvas_states.add_suggestion(
                msg.author, suggestion_text, theater_id=theater_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "ok", "type": "suggestion", "suggestion": suggestion}

    canvas_states.add_chat_message(msg.text, author=msg.author, theater_id=theater_id)
    return {"status": "ok", "type": "chat"}


@app.get("/api/suggestions")
def get_suggestions(request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)
    return canvas_states.get_suggestions(theater_id)


@app.post("/api/suggestions/upvote")
def upvote_suggestion(vote: SuggestionVote, request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)
    if not canvas_states.upvote_suggestion(vote.voter, vote.target_author, theater_id):
        raise HTTPException(status_code=404, detail="Suggestion not found or cannot be upvoted.")
    return {"status": "ok", "type": "suggestion"}


@app.post("/api/suggestions/withdraw")
def withdraw_suggestion(withdrawal: SuggestionWithdrawal, request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)
    if not canvas_states.withdraw_suggestion(withdrawal.author, theater_id):
        raise HTTPException(status_code=404, detail="Suggestion not found.")
    return {"status": "ok", "type": "suggestion"}


@app.post("/api/theaters/{theater_id}/collab")
def set_viewer_collab_mode(
    theater_id: str,
    payload: ViewerCollabRequest,
    request: Request,
):
    """Enable or disable audience collaboration for a theater owner."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    deployment = db.get_deployment(theater_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Active theater not found.")
    if deployment["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Only the theater owner can change collaboration mode.")

    canvas_states.set_viewer_collab_enabled(payload.enabled, theater_id)
    return {
        "theater_id": theater_id,
        "viewer_collab_enabled": payload.enabled,
    }

@app.get("/api/stats")
def get_stats_api():
    """Retrieve system stats summary (accounts, 7-day active users, theater views)."""
    return db.get_stats_summary()
