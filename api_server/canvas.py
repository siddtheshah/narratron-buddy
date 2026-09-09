"""Canvas WebSocket, chat, orator control, and stats API endpoints."""

import asyncio
from typing import Any, Optional

from fastapi import Request, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel, Field
from google.genai import types

from api_server.shared import (
    app,
    config,
    canvas_states,
    db,
    theater_manager,
    theater_repository,
    get_current_user,
    get_current_user_async,
    _require_canvas_access,
    _require_canvas_access_async,
    can_control_agent_websocket,
)
from api_server.dependencies import agent_manager


class ChatMessage(BaseModel):
    author: str
    text: str


class OratorCommand(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class SuggestionVote(BaseModel):
    voter: str
    target_author: str


class SuggestionWithdrawal(BaseModel):
    author: str


class ViewerCollabRequest(BaseModel):
    enabled: bool


class A2UIActionBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    surfaceId: str = Field(min_length=1, max_length=100)
    sourceComponentId: str = Field(min_length=1, max_length=100)
    timestamp: str = Field(min_length=1, max_length=100)
    context: dict = Field(default_factory=dict)
    wantResponse: bool = False


class A2UIActionEnvelope(BaseModel):
    version: str
    action: A2UIActionBody


class A2UISurfacePlacement(BaseModel):
    left_pct: float = Field(ge=2, le=98)
    top_pct: float = Field(ge=2, le=98)


# ========================================
# Canvas & WebSocket Endpoints
# ========================================

def _state(theater_id: Optional[str] = None):
    """Resolve the theater coordinator before selecting one of its components."""
    return canvas_states.get(theater_id)


def _unregister_doodle_websocket(state: Any, websocket: WebSocket) -> None:
    connections = state.connections
    if websocket in connections.active_ws_connections:
        connections.active_ws_connections.remove(websocket)
    connections.active_user_connections.pop(websocket, None)


async def _broadcast_doodle(state: Any, message: dict[str, object], sender: WebSocket | None = None) -> None:
    connections = state.connections
    recipients = [connection for connection in connections.active_ws_connections if connection is not sender]
    results = await asyncio.gather(*(connection.send_json(message) for connection in recipients), return_exceptions=True)
    for connection, result in zip(recipients, results):
        if isinstance(result, BaseException):
            _unregister_doodle_websocket(state, connection)


async def _broadcast_state_message(state: Any, message: dict[str, object]) -> None:
    connections = state.connections
    recipients = list(connections.active_state_ws_connections)
    results = await asyncio.gather(*(connection.send_json(message) for connection in recipients), return_exceptions=True)
    for connection, result in zip(recipients, results):
        if isinstance(result, BaseException) and connection in connections.active_state_ws_connections:
            connections.active_state_ws_connections.remove(connection)


def _enable_state_notifications(state: Any) -> None:
    """Bind component invalidations to this route module's WebSocket transport."""
    connections = state.connections
    if getattr(connections, "_api_state_notification_transport", False):
        return

    def notify(*domains: str) -> None:
        connections.state_revision += 1
        loop = connections.state_ws_loop
        if not connections.active_state_ws_connections or loop is None or loop.is_closed():
            return
        payload = {
            "type": "state_changed",
            "revision": connections.state_revision,
            "domains": sorted(set(domains)),
        }
        try:
            running_loop = asyncio.get_running_loop()
            if running_loop is loop:
                loop.create_task(_broadcast_state_message(state, payload))
            else:
                asyncio.run_coroutine_threadsafe(_broadcast_state_message(state, payload), loop)
        except RuntimeError:
            pass

    connections.notify = notify
    connections._api_state_notification_transport = True


async def broadcast_baton_update(theater_id: str, baton_state: dict[str, object]) -> None:
    """Send the baton state through the theater's doodle transport."""
    state = _state(theater_id)
    users = {
        user["id"]: {"id": user["id"], "username": user.get("username", "")}
        for user in state.connections.active_user_connections.values()
        if user and "id" in user
    }
    await _broadcast_doodle(state, {"type": "baton_state", "baton_state": baton_state, "active_viewers": list(users.values())})


def _interactive_action(state: Any, surface_id: str, component_id: str, action_name: str) -> dict[str, object] | None:
    surface = state.ui.interactive_surfaces.get(surface_id, {})
    for message in surface.get("messages", []) if isinstance(surface, dict) else []:
        payload = message.get("createSurface") or message.get("updateComponents") or {} if isinstance(message, dict) else {}
        for component in payload.get("components", []) if isinstance(payload, dict) else []:
            if not isinstance(component, dict) or str(component.get("id")) != component_id:
                continue
            event = (component.get("action") or {}).get("event", {})
            if isinstance(event, dict) and event.get("name") == action_name:
                return dict(event)
    return None


async def _apply_doodle_message(state: Any, data: dict[str, object], sender: WebSocket) -> None:
    """Validate, persist, and relay the browser doodle protocol."""
    connections = state.connections
    message_id = data.get("client_message_id")
    message_id = message_id if isinstance(message_id, str) and len(message_id) <= 128 else None

    async def acknowledge() -> None:
        if not message_id:
            return
        connections.processed_doodle_message_ids.add(message_id)
        if len(connections.processed_doodle_message_ids) > 2_000:
            connections.processed_doodle_message_ids.clear()
            connections.processed_doodle_message_ids.add(message_id)
        await sender.send_json({"type": "doodle_ack", "client_message_id": message_id})

    if message_id and message_id in connections.processed_doodle_message_ids:
        await sender.send_json({"type": "doodle_ack", "client_message_id": message_id})
        return

    if data.get("type") == "toggle_doodles":
        state.doodles.enabled = bool(data.get("enabled", True))
        state.persist()
        await _broadcast_doodle(state, {"type": "doodles_toggle", "enabled": state.doodles.enabled})
        await acknowledge()
        return

    if data.get("type") == "draw_batch":
        color, size, points = data.get("color"), data.get("size", 3), data.get("points")
        if not isinstance(points, list) or len(points) < 4 or len(points) % 2 or len(points) > 400:
            return
        try:
            normalized_points = [float(point) for point in points]
            normalized_size = float(size)
        except (TypeError, ValueError):
            return
        if not all(0 <= point <= 1 for point in normalized_points) or not 1 <= normalized_size <= 100:
            return
        state.doodles.add([
            {"type": "draw", "x0": normalized_points[index], "y0": normalized_points[index + 1],
             "x1": normalized_points[index + 2], "y1": normalized_points[index + 3],
             "color": color, "size": normalized_size}
            for index in range(0, len(normalized_points) - 2, 2)
        ])
        await _broadcast_doodle(state, {"type": "draw_batch", "color": color, "size": normalized_size,
                                        "points": normalized_points}, sender)
        await acknowledge()
        return

    if data.get("type") in {"clear", "draw"}:
        state.doodles.add([data])
        await _broadcast_doodle(state, data, sender)
        await acknowledge()

@app.websocket("/ws/doodle")
async def websocket_endpoint(websocket: WebSocket, theater_id: Optional[str] = None):
    if theater_id:
        try:
            await _require_canvas_access_async(websocket, theater_id)
        except HTTPException:
            await websocket.close(code=1008)
            return
    await websocket.accept()
    websocket.state.theater_id = theater_id
    current_user = await get_current_user_async(websocket)
    cs = _state(theater_id)
    connections = cs.connections
    if websocket not in connections.active_ws_connections:
        connections.active_ws_connections.append(websocket)
    connections.active_user_connections[websocket] = current_user
    await websocket.send_json({"type": "doodles_toggle", "enabled": cs.doodles.enabled})
    await websocket.send_json({"type": "doodle_snapshot", "batches": cs.doodles.snapshot_batches()})
    
    if theater_id:
        baton_st = await db.get_theater_baton_state_async(theater_id)
        if baton_st:
            await broadcast_baton_update(theater_id, baton_st)

    try:
        while True:
            data = await websocket.receive_json()
            await _apply_doodle_message(cs, data, websocket)
    except WebSocketDisconnect:
        _unregister_doodle_websocket(cs, websocket)
        if theater_id:
            baton_st = await db.get_theater_baton_state_async(theater_id)
            if baton_st:
                await broadcast_baton_update(theater_id, baton_st)


@app.websocket("/ws/canvas-state")
async def canvas_state_websocket_endpoint(websocket: WebSocket, theater_id: Optional[str] = None):
    """Notification-only state channel; REST remains the source of truth."""
    if theater_id:
        try:
            await _require_canvas_access_async(websocket, theater_id)
        except HTTPException:
            await websocket.close(code=1008)
            return
    await websocket.accept()
    state = _state(theater_id)
    connections = state.connections
    _enable_state_notifications(state)
    if websocket not in connections.active_state_ws_connections:
        connections.active_state_ws_connections.append(websocket)
    connections.state_ws_loop = asyncio.get_running_loop()
    await websocket.send_json({"type": "state_ready", "revision": connections.state_revision})
    try:
        # This endpoint accepts no application commands. Receiving here only
        # lets the server promptly notice a disconnected browser.
        while True:
            await websocket.receive()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if websocket in connections.active_state_ws_connections:
            connections.active_state_ws_connections.remove(websocket)


@app.api_route("/api/orator/toggle_mic", methods=["GET", "POST"])
async def trigger_orator_mic_toggle(request: Request, theater_id: Optional[str] = None):
    user = await get_current_user_async(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to control Orator microphone.")
    
    if theater_id:
        dep = db.get_deployment(theater_id)
        if dep and dep["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Permission denied. Only the theater owner can control the Orator microphone.")

    states = [_state(theater_id)] if theater_id else list(canvas_states.states.values())
    count = 0
    for state in states:
        for websocket in list(state.connections.active_ws_connections):
            try:
                await websocket.send_json({"type": "toggle_mic"})
                count += 1
            except Exception:
                _unregister_doodle_websocket(state, websocket)
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
            theater_repository.reconstruct_theater(theater_id, theater_dir)
    return _state(theater_id).get_latest_state()

@app.get("/api/chat")
def get_chat(request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)
    return _state(theater_id).chat.get_messages()


@app.post("/api/a2ui/action")
def post_a2ui_action(payload: A2UIActionEnvelope, request: Request, theater_id: str):
    """Validate a renderer action, then relay it to the active live agent."""
    _require_canvas_access(request, theater_id)
    if payload.version != "v1.0":
        raise HTTPException(status_code=400, detail="Unsupported A2UI protocol version.")
    deployment = db.get_deployment(theater_id)
    current_user = get_current_user(request)
    if not can_control_agent_websocket(deployment, current_user=current_user):
        raise HTTPException(status_code=403, detail="Only the active orator can use interactive canvas controls.")
    state = _state(theater_id)
    action = _interactive_action(
        state,
        payload.action.surfaceId,
        payload.action.sourceComponentId,
        payload.action.name,
    )
    if not action:
        raise HTTPException(status_code=404, detail="This interactable is no longer active.")
    authoritative_context = action.get("context") or {}
    user_action = " ".join(str(
        authoritative_context.get("userAction") or authoritative_context.get("playerAction") or ""
    ).split())[:2000]
    if not user_action:
        raise HTTPException(status_code=400, detail="Interactive control has no user action.")
    session = agent_manager.get_session(theater_id)
    if not session or not session.is_alive:
        raise HTTPException(status_code=409, detail="The live agent is not connected.")
    notification = (
        "[A2UI Canvas Action] The active user selected this immutable user input: "
        f"{user_action!r}. In Adventure Mode, submit in-world actions through process_user_action "
        "without rewriting them. Otherwise handle this selection directly as explicit user input."
    )
    if not session.send_user_content(types.Content(parts=[types.Part(text=notification)])):
        raise HTTPException(status_code=409, detail="The live agent could not receive the action.")
    state.ui.delete_surface(payload.action.surfaceId)
    return {"status": "accepted", "surface_id": payload.action.surfaceId}


@app.post("/api/orator/command")
def post_orator_command(command: OratorCommand, request: Request, theater_id: str):
    """Relay a direct typed instruction from the active orator to Live."""
    _require_canvas_access(request, theater_id)
    deployment = db.get_deployment(theater_id)
    current_user = get_current_user(request)
    if not can_control_agent_websocket(deployment, current_user=current_user):
        raise HTTPException(status_code=403, detail="Only the active orator can send commands to Narratron.")

    text = " ".join(command.text.split())
    if not text:
        raise HTTPException(status_code=400, detail="A command cannot be empty.")

    session = agent_manager.get_session(theater_id)
    if not session or not session.is_alive:
        raise HTTPException(status_code=409, detail="The live agent is not connected.")

    notification = (
        "[Orator Command] The active orator typed this direct instruction: "
        f"{text!r}. Treat it as explicit user input. In Adventure Mode, submit an in-world "
        "action through process_user_action exactly as written; otherwise handle it directly."
    )
    if not session.send_user_content(types.Content(parts=[types.Part(text=notification)])):
        raise HTTPException(status_code=409, detail="The live agent could not receive the command.")
    return {"status": "accepted"}


@app.patch("/api/a2ui/surfaces/{surface_id}")
def move_a2ui_surface(
    surface_id: str, payload: A2UISurfacePlacement, request: Request, theater_id: str
):
    """Move a generated surface for all connected canvas viewers."""
    _require_canvas_access(request, theater_id)
    deployment = db.get_deployment(theater_id)
    current_user = get_current_user(request)
    if not can_control_agent_websocket(deployment, current_user=current_user):
        raise HTTPException(status_code=403, detail="Only the active orator can move interactables.")
    placement = _state(theater_id).ui.move_surface(surface_id, payload.left_pct, payload.top_pct)
    if placement is None:
        raise HTTPException(status_code=404, detail="This interactable is no longer active.")
    return {"status": "moved", "surface_id": surface_id, "placement": placement}


@app.delete("/api/a2ui/surfaces/{surface_id}")
def delete_a2ui_surface(surface_id: str, request: Request, theater_id: str):
    """Let the active orator remove an unwanted generated surface."""
    _require_canvas_access(request, theater_id)
    deployment = db.get_deployment(theater_id)
    current_user = get_current_user(request)
    if not can_control_agent_websocket(deployment, current_user=current_user):
        raise HTTPException(status_code=403, detail="Only the active orator can delete interactables.")
    if not _state(theater_id).ui.delete_surface(surface_id):
        raise HTTPException(status_code=404, detail="This interactable is no longer active.")
    return {"status": "deleted", "surface_id": surface_id}

@app.post("/api/chat")
def post_chat(msg: ChatMessage, request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)

    user = get_current_user(request, record_activity=False)
    author = user["username"] if user else msg.author.strip()
    profile_username = user["username"] if user else None
    profile_color = user.get("profile_color") if user else None
    command_parts = msg.text.strip().split(maxsplit=1)
    if command_parts and command_parts[0].lower() == "/suggest":
        suggestion_text = command_parts[1] if len(command_parts) > 1 else ""
        if not suggestion_text:
            raise HTTPException(status_code=400, detail="A suggestion must include text after /suggest.")
        try:
            suggestion_kwargs = {}
            if profile_username:
                suggestion_kwargs["profile_username"] = profile_username
                if profile_color:
                    suggestion_kwargs["profile_color"] = profile_color
            state = _state(theater_id)
            suggestion = state.chat.add_suggestion(author, suggestion_text, **suggestion_kwargs)
            state.notify_changed("chat", "suggestions")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "ok", "type": "suggestion", "suggestion": suggestion}

    chat_kwargs = {"author": author}
    if profile_username:
        chat_kwargs["profile_username"] = profile_username
        if profile_color:
            chat_kwargs["profile_color"] = profile_color
    state = _state(theater_id)
    state.chat.add_message({"text": msg.text, **chat_kwargs})
    state.notify_changed("chat")
    return {"status": "ok", "type": "chat"}


@app.get("/api/suggestions")
def get_suggestions(request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)
    return _state(theater_id).chat.get_suggestions()


@app.get("/api/sticky-notes")
def get_sticky_notes(request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)

    hidden_stickies = []
    if theater_id:
        try:
            th_cfg = theater_manager.get_theater_config(theater_id)
            sp_cfg = th_cfg.get("story_planning", {}) if isinstance(th_cfg.get("story_planning"), dict) else {}
            raw_hidden = sp_cfg.get("hidden_stickies", th_cfg.get("hidden_stickies", []))
            if isinstance(raw_hidden, (list, tuple, set)):
                hidden_stickies = [str(x.get("topic", x.get("name", x)) if isinstance(x, dict) else x).strip() for x in raw_hidden if x]
            elif isinstance(raw_hidden, str):
                hidden_stickies = [s.strip() for s in raw_hidden.split(",") if s.strip()]
            elif isinstance(raw_hidden, dict):
                hidden_stickies = [str(k).strip() for k in raw_hidden.keys() if str(k).strip()]
            structured = sp_cfg.get("stickies", {})
            if isinstance(structured, dict):
                hidden_stickies.extend(
                    str(topic).strip()
                    for topic, definition in structured.items()
                    if isinstance(definition, dict) and definition.get("hidden")
                )
                hidden_stickies = list(dict.fromkeys(hidden_stickies))
        except Exception:
            hidden_stickies = []

    session = agent_manager.get_session(theater_id) if theater_id else None
    session_tools = getattr(session, "story_planning_tools", None) or getattr(session, "named_element_tools", None) if session else None
    if session_tools and hasattr(session_tools, "get_present_sticky_notes"):
        notes = session_tools.get_present_sticky_notes()
        return {"sticky_notes": notes, "hidden_stickies": hidden_stickies, "count": len(notes)}
    elif session_tools and hasattr(session_tools, "get_present_elements"):
        notes = session_tools.get_present_elements()
        return {"sticky_notes": notes, "hidden_stickies": hidden_stickies, "count": len(notes)}
    notes = _state(theater_id).story.sticky_notes()
    return {"sticky_notes": notes, "hidden_stickies": hidden_stickies, "count": len(notes)}



@app.post("/api/suggestions/upvote")
def upvote_suggestion(vote: SuggestionVote, request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)
    state = _state(theater_id)
    if not state.chat.upvote_suggestion(vote.voter, vote.target_author):
        raise HTTPException(status_code=404, detail="Suggestion not found or cannot be upvoted.")
    state.notify_changed("suggestions")
    return {"status": "ok", "type": "suggestion"}


@app.post("/api/suggestions/withdraw")
def withdraw_suggestion(withdrawal: SuggestionWithdrawal, request: Request, theater_id: Optional[str] = None):
    if theater_id:
        _require_canvas_access(request, theater_id)
    state = _state(theater_id)
    if not state.chat.withdraw_suggestion(withdrawal.author):
        raise HTTPException(status_code=404, detail="Suggestion not found.")
    state.notify_changed("chat", "suggestions")
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

    _state(theater_id).ui.set_viewer_collab_enabled(payload.enabled)
    session = agent_manager.get_session(theater_id)
    if session:
        session.send_collaboration_toggle_observability()
    return {
        "theater_id": theater_id,
        "viewer_collab_enabled": payload.enabled,
    }

@app.get("/api/stats")
def get_stats_api():
    """Retrieve system stats summary (accounts, 7-day active users, theater views)."""
    return db.get_stats_summary()
