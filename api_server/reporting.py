"""User reports: theater moderation evidence, bug reports, and suggestions."""

import asyncio
import logging
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, JsonValue

from api_server.shared import (
    app, db, theater_manager, theater_repository, canvas_states,
    get_current_user_async, _require_canvas_access_async, _safe_path_param,
)
from storage.flagged_theaters import snapshot_theater

logger = logging.getLogger(__name__)


class ReportTheaterRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=80)
    details: str = Field(min_length=1, max_length=4000)


_report_locks: dict[str, asyncio.Lock] = {}


@app.post("/api/theaters/{theater_id}/reports", status_code=201)
async def report_theater(
    theater_id: str, body: ReportTheaterRequest, request: Request,
) -> dict[str, JsonValue]:
    _safe_path_param(theater_id, "theater_id")
    user = await get_current_user_async(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to report a theater.")
    await _require_canvas_access_async(request, theater_id)
    reason, details = body.reason.strip(), body.details.strip()
    if reason not in {"harmful_content", "harassment", "sexual_content", "illegal_content", "other"} or not details:
        raise HTTPException(status_code=422, detail="Select a reason and describe the problem.")
    deployment = await asyncio.to_thread(db.get_deployment, theater_id)
    if not deployment or deployment["user_id"] is None:
        raise HTTPException(status_code=404, detail="Theater not found.")

    def capture_report() -> int:
        root = theater_manager.theater(theater_id).directory()
        if not (root / "theater.json").is_file():
            if not theater_repository.reconstruct_theater(theater_id, root):
                raise FileNotFoundError("Theater files are unavailable")
        canvas_states.get(theater_id).save_local_theater_data(theater_dir=root)
        content_hash, storage_path = snapshot_theater(
            root, theater_repository.base_dir.parent / "flagged_theaters", theater_id,
        )
        return db.record_theater_report(
            theater_id, deployment.get("name") or theater_id,
            int(deployment["user_id"]),
            int(deployment.get("active_orator_id") or deployment["user_id"]),
            int(user["id"]), reason, details, content_hash, storage_path,
        )

    try:
        async with _report_locks.setdefault(theater_id, asyncio.Lock()):
            report_id = await asyncio.to_thread(capture_report)
    except Exception as error:
        logger.exception("Could not store theater report for %s", theater_id)
        raise HTTPException(status_code=503, detail="Could not save your report. Please try again.") from error
    return {"status": "ok", "report_id": report_id}


class FeedbackRequest(BaseModel):
    category: Literal["bug", "suggestion"]
    details: str = Field(min_length=1, max_length=4000)
    theater_id: str | None = Field(default=None, min_length=1, max_length=128)


@app.post("/api/reports", status_code=201)
async def file_feedback(body: FeedbackRequest, request: Request) -> dict[str, JsonValue]:
    """File product feedback, optionally with the current theater as context."""
    user = await get_current_user_async(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to file a bug or suggestion.")
    details = body.details.strip()
    if not details:
        raise HTTPException(status_code=422, detail="Describe the bug or improvement suggestion.")
    if body.theater_id is not None:
        _safe_path_param(body.theater_id, "theater_id")
        await _require_canvas_access_async(request, body.theater_id)
    try:
        report_id = await asyncio.to_thread(
            db.record_user_feedback, int(user["id"]), body.category, details, body.theater_id,
        )
    except Exception as error:
        logger.exception("Could not save user feedback")
        raise HTTPException(status_code=503, detail="Could not save your feedback. Please try again.") from error
    return {"status": "ok", "report_id": report_id}


