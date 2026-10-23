"""Public user profile routes and owner profile settings."""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlsplit

from fastapi import File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field, JsonValue

from api_server.shared import app, db, get_current_user, get_current_user_async, stamp_storage
from api_server.dependencies import pricing_controller
from services.generation_billing import billing_locks, MAX_CONCURRENT_GENERATION_JOBS, GENERATION_SLOT_TTL_SECONDS
from services.theater_image_generation import generate_theater_image
from storage.stamp_storage import process_stamp_image
from storage.storage_limits import MAX_FILE_UPLOAD_BYTES
from utils.auth_cache import auth_session_cache

logger = logging.getLogger(__name__)


class GenerateProfileStampRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=4000)


@app.post("/api/users/me/stamps/generate")
async def generate_profile_stamp(request: Request, body: GenerateProfileStampRequest) -> dict[str, JsonValue]:
    """Generate and save a personal stamp at the standard image credit rate."""
    user = await get_current_user_async(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to generate stamps.")
    name, prompt = body.name.strip(), body.prompt.strip()
    if not name or not prompt:
        raise HTTPException(status_code=422, detail="Enter a stamp name and image prompt.")
    user_id = int(user["id"])
    slot_id = await asyncio.to_thread(
        db.acquire_generation_slot, user_id, "stamp", f"profile:{user_id}",
        MAX_CONCURRENT_GENERATION_JOBS, GENERATION_SLOT_TTL_SECONDS,
    )
    if slot_id is None:
        raise HTTPException(status_code=429, detail="Too many concurrent generation jobs. Please try again shortly.")
    try:
        async with billing_locks.setdefault(user_id, asyncio.Lock()):
            if await asyncio.to_thread(db.count_user_stamps, user_id) >= 10:
                raise HTTPException(status_code=400, detail="Maximum limit of 10 stamps reached. Delete a stamp first.")
            cost = pricing_controller.get_rates()["image_credit_rate"]
            account = await asyncio.to_thread(db.get_user_by_id, user_id)
            if not account or account["credits"] < cost:
                raise HTTPException(status_code=402, detail=f"Generation requires {cost:g} credits. Top up on /deploy.")
        try:
            image = await asyncio.to_thread(generate_theater_image, None, kind="stamp", prompt=prompt, references=[])
            processed_bytes, content_type = await asyncio.to_thread(process_stamp_image, image.image_bytes)
        except Exception as error:
            logger.exception("Profile stamp generation failed")
            raise HTTPException(status_code=502, detail="Stamp generation failed; no credits were charged.") from error

        async with billing_locks.setdefault(user_id, asyncio.Lock()):
            # Recheck capacity after generation; uploads and other jobs may have filled it.
            if await asyncio.to_thread(db.count_user_stamps, user_id) >= 10:
                raise HTTPException(status_code=400, detail="Maximum limit of 10 stamps reached; no credits were charged.")
            try:
                filename = await asyncio.to_thread(stamp_storage.save_stamp, user_id, "stamp.webp", processed_bytes, content_type)
            except Exception as error:
                logger.exception("Profile stamp storage failed")
                raise HTTPException(status_code=502, detail="Could not save stamp; no credits were charged.") from error
            try:
                stamp = await asyncio.to_thread(db.create_user_stamp, user_id, name, filename, content_type)
            except ValueError as error:
                await asyncio.to_thread(stamp_storage.delete_stamp, user_id, filename)
                raise HTTPException(status_code=400, detail=str(error)) from error
            except Exception as error:
                await asyncio.to_thread(stamp_storage.delete_stamp, user_id, filename)
                logger.exception("Profile stamp storage failed")
                raise HTTPException(status_code=502, detail="Could not save stamp; no credits were charged.") from error
            try:
                updated = await asyncio.to_thread(
                    db.record_user_usage, user_id, images_created=1, credit_cost=cost,
                    idempotency_key=f"profile-stamp:{user_id}:{stamp['id']}:{filename}",
                )
            except Exception as error:
                await asyncio.to_thread(db.delete_user_stamp, int(stamp["id"]), user_id)
                await asyncio.to_thread(stamp_storage.delete_stamp, user_id, filename)
                logger.exception("Profile stamp billing failed")
                raise HTTPException(status_code=503, detail="Could not settle generation credits. Please try again.") from error
            auth_session_cache.invalidate_user(user_id)
        return {"stamp": {**stamp, "url": f"/api/stamps/{stamp['id']}"}, "credits_charged": cost, "credits": float(updated["credits"])}
    finally:
        await asyncio.to_thread(db.release_generation_slot, slot_id)


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


@app.get("/api/users/me/stamps")
def get_my_stamps_endpoint(request: Request) -> list[dict[str, Union[int, str]]]:
    """List all stamps for the authenticated user."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    stamps = db.get_user_stamps(int(user["id"]))
    return [
        {
            **stamp,
            "url": f"/api/stamps/{stamp['id']}",
        }
        for stamp in stamps
    ]


@app.get("/api/users/{username}/stamps")
def get_user_stamps_endpoint(username: str, request: Request) -> list[dict[str, Union[int, str]]]:
    """List all stamps for the specified user."""
    _profile_or_404(username, request)
    target_user = db.get_user_by_username(username)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found.")
    stamps = db.get_user_stamps(int(target_user["id"]))
    return [
        {
            **stamp,
            "url": f"/api/stamps/{stamp['id']}",
        }
        for stamp in stamps
    ]


@app.post("/api/users/me/stamps")
async def upload_stamp(
    request: Request,
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
) -> dict[str, Union[int, str]]:
    """Upload a new stamp for the authenticated user (maximum 10 stamps)."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    user_id = int(user["id"])

    current_count = db.count_user_stamps(user_id)
    if current_count >= 10:
        raise HTTPException(
            status_code=400,
            detail="Maximum limit of 10 stamps reached. Please delete an existing stamp before uploading a new one.",
        )

    file_bytes = await file.read(MAX_FILE_UPLOAD_BYTES + 1)
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded image file is empty.")
    if len(file_bytes) > MAX_FILE_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Uploaded file exceeds 10MB limit.")

    try:
        processed_bytes, content_type = process_stamp_image(file_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    raw_filename = file.filename or "stamp.png"
    stamp_name = (name or "").strip()
    if not stamp_name:
        stamp_name = Path(raw_filename).stem.strip() or "stamp"
    if len(stamp_name) > 100:
        stamp_name = stamp_name[:100]

    stored_filename = stamp_storage.save_stamp(user_id, raw_filename, processed_bytes, content_type)
    try:
        stamp_record = db.create_user_stamp(user_id, stamp_name, stored_filename, content_type)
    except ValueError as exc:
        stamp_storage.delete_stamp(user_id, stored_filename)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    auth_session_cache.invalidate_user(user_id)
    return {
        **stamp_record,
        "url": f"/api/stamps/{stamp_record['id']}",
    }


@app.delete("/api/users/me/stamps/{stamp_id}")
def delete_stamp_endpoint(stamp_id: int, request: Request) -> dict[str, str]:
    """Delete a stamp owned by the authenticated user."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    user_id = int(user["id"])

    stamp = db.get_user_stamp(stamp_id, user_id)
    if not stamp:
        raise HTTPException(status_code=404, detail="Stamp not found.")

    filename = str(stamp["filename"])
    stamp_storage.delete_stamp(user_id, filename)
    db.delete_user_stamp(stamp_id, user_id)
    auth_session_cache.invalidate_user(user_id)
    return {"status": "ok", "message": "Stamp deleted successfully."}


@app.get("/api/stamps/{stamp_id}")
def get_stamp_endpoint(stamp_id: int) -> Response:
    """Stream stamp image bytes for display."""
    stamp = db.get_stamp_by_id(stamp_id)
    if not stamp:
        raise HTTPException(status_code=404, detail="Stamp not found.")

    image_bytes = stamp_storage.read_stamp(int(stamp["user_id"]), str(stamp["filename"]))
    if not image_bytes:
        raise HTTPException(status_code=404, detail="Stamp image file not found.")

    return Response(content=image_bytes, media_type=str(stamp["content_type"]))
