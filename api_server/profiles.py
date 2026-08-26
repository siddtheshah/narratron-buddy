"""Public user profile routes and owner profile settings."""

import os
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, Field

from api_server.shared import app, db, get_current_user
from utils.auth_cache import auth_session_cache


class ProfileUpdate(BaseModel):
    bio: str = ""
    stats_visible: bool = False
    profile_color: str = "#818cf8"


class CreditGiftRequest(BaseModel):
    credits: float = Field(gt=0)


def _public_base_url() -> str:
    """Return the operator-configured canonical origin for bearer-link URLs."""
    value = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        not value
        or parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(
            status_code=500,
            detail="PUBLIC_BASE_URL must be a canonical absolute origin, such as https://app.example.com.",
        )
    return value


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


@app.post("/api/credit-gifts")
def create_credit_gift(payload: CreditGiftRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    public_base_url = _public_base_url()
    try:
        gift = db.create_credit_gift(user["id"], payload.credits)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**gift, "link": f"{public_base_url}/gift/{gift['token']}"}


@app.get("/api/credit-gifts/{token}")
def get_credit_gift(token: str):
    gift = db.get_credit_gift(token)
    if not gift:
        raise HTTPException(status_code=404, detail="Gift link not found.")
    return gift


@app.post("/api/credit-gifts/{token}/claim")
def claim_credit_gift(token: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to claim this gift.")
    try:
        gift = db.claim_credit_gift(token, user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    auth_session_cache.invalidate_user(user["id"])
    auth_session_cache.invalidate_user(gift.pop("sender_user_id"))
    return {"status": "claimed", **gift}


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
