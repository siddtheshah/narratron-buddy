"""Authenticated friend requests and friend-based theater admission."""

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, JsonValue

from api_server.dependencies import canvas_states, theater_manager
from api_server.shared import app, db, get_current_user, _grant_canvas_access


class FriendAction(BaseModel):
    action: Literal["request", "accept", "remove"]


def _user_id(request: Request) -> int:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return int(user["id"])


@app.get("/api/friends")
def list_friends(request: Request, response: Response) -> list[dict[str, JsonValue]]:
    response.headers["Cache-Control"] = "no-store"
    return db.get_friendships(_user_id(request))


@app.post("/api/friends/{username}")
def update_friend(username: str, payload: FriendAction, request: Request) -> dict[str, str]:
    try:
        db.friend_action(_user_id(request), username, payload.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


@app.get("/api/friend-theaters")
def list_friend_theaters(request: Request, response: Response) -> list[dict[str, JsonValue]]:
    response.headers["Cache-Control"] = "no-store"
    theaters = db.get_friend_theaters(_user_id(request))
    now = datetime.now(timezone.utc)
    for theater in theaters:
        theater.pop("join_key")
        canvas = canvas_states.states.get(str(theater["theater_id"]))
        theater["active_viewers"] = [
            str(viewer["username"])
            for viewer in canvas.connections.get_active_viewers()
            if viewer["username"]
        ] if canvas is not None else []
        metadata = theater_manager.get_theater(str(theater["theater_id"]))
        theater["online"] = False
        if metadata is None or not metadata.last_connected_at:
            continue
        try:
            last_connected = datetime.fromisoformat(metadata.last_connected_at)
        except ValueError:
            continue
        if last_connected.tzinfo is None:
            last_connected = last_connected.replace(tzinfo=timezone.utc)
        theater["online"] = timedelta(0) <= now - last_connected < timedelta(minutes=10)
    return theaters


@app.post("/api/friend-theaters/{theater_id}/join")
def join_friend_theater(theater_id: str, request: Request, response: Response) -> dict[str, str]:
    # Recheck the relationship at admission time, including after removal or a key rotation.
    theaters = db.get_friend_theaters(_user_id(request))
    theater = next((entry for entry in theaters if entry["theater_id"] == theater_id), None)
    if theater is None:
        raise HTTPException(status_code=404, detail="Friend's theater is no longer available.")
    _grant_canvas_access(response, request, theater_id, str(theater["join_key"]))
    return {"theater_id": theater_id}
