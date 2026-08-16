"""Public user profile routes and owner profile settings."""

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel

from api_server.shared import app, db, get_current_user
from utils.auth_cache import auth_session_cache


class ProfileUpdate(BaseModel):
    bio: str = ""
    stats_visible: bool = False
    profile_color: str = "#818cf8"


def _profile_or_404(username: str, request: Request):
    viewer = get_current_user(request, record_activity=False)
    profile = db.get_user_profile(username, viewer["id"] if viewer else None)
    if not profile:
        raise HTTPException(status_code=404, detail="User profile not found.")
    return profile


@app.get("/api/users/{username}/profile")
def get_profile(username: str, request: Request):
    return _profile_or_404(username, request)


@app.put("/api/users/me/profile")
def update_my_profile(payload: ProfileUpdate, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        db.update_user_profile(user["id"], payload.bio, payload.stats_visible, payload.profile_color)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    auth_session_cache.invalidate_user(user["id"])
    return _profile_or_404(user["username"], request)


@app.delete("/api/users/me")
def delete_my_account(request: Request, response: Response):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    user_id = user["id"]
    token = request.cookies.get("auth_token")
    if token:
        db.invalidate_session_token(token)
        auth_session_cache.invalidate_token(token)
    auth_session_cache.invalidate_user(user_id)
    db.delete_user(user_id)
    response.delete_cookie("auth_token")
    return {"status": "ok", "message": "Account deleted successfully."}

