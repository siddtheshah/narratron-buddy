"""Authenticated friend requests and friend-based theater admission."""

from typing import Literal

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, JsonValue

from api_server.dependencies import live_agent_manager
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
    for theater in theaters:
        theater.pop("join_key")
        session = live_agent_manager.get_session(str(theater["theater_id"]))
        theater["online"] = bool(session and session.is_online)
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
