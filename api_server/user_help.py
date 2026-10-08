"""Private paid canvas help and free visitor help using the same service."""

import asyncio
import os
import time
from uuid import uuid4

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, Field

from api_server.dependencies import pricing_controller
from api_server.shared import app, db, get_current_user_async, _require_canvas_access_async
from services.generation_billing import billing_locks
from services.user_help_service import HelpUnavailableError, UserHelpService
from utils.auth_cache import auth_session_cache
from utils.markdown import render_markdown

_PUBLIC_COOLDOWN_SECONDS = 15.0
_public_requests: dict[str, float] = {}
_active_public_requests: set[str] = set()


class HelpQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class HelpAnswer(BaseModel):
    author: str = "Narratron User Help"
    type: str = "user_help"
    text: str
    html: str
    credits_charged: float = 0.0
    credits: float | None = None


def help_cost() -> float:
    """Price direct help as one assistant invocation."""
    return float(pricing_controller.get_rates()["live_agent_tool_call_credit_rate"])


async def research(question: str) -> str:
    service = UserHelpService(model=os.environ.get("USER_HELP_MODEL", "gemini-3.7-flash"))
    try:
        return await service.answer(question)
    except HelpUnavailableError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/api/user-help")
def user_help_info(response: Response) -> dict[str, float]:
    response.headers["Cache-Control"] = "no-store"
    return {"canvas_credit_cost": help_cost()}


@app.post("/api/user-help", response_model=HelpAnswer)
async def ask_user_help(
    body: HelpQuestion,
    request: Request,
    response: Response,
    theater_id: str | None = None,
    join_key: str | None = None,
) -> HelpAnswer:
    """Return help only to the caller; never write to shared theater state."""
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Please enter a question.")
    response.headers["Cache-Control"] = "no-store"
    if theater_id is not None:
        if not theater_id.strip():
            raise HTTPException(status_code=400, detail="theater_id is required.")
        user = await get_current_user_async(request)
        if not user:
            raise HTTPException(status_code=401, detail="Sign in to ask private canvas help.")
        await _require_canvas_access_async(request, theater_id.strip(), join_key=join_key)
        user_id = int(user["id"])
        async with billing_locks.setdefault(user_id, asyncio.Lock()):
            cost = help_cost()
            account = await asyncio.to_thread(db.get_user_by_id, user_id)
            if not account or float(account["credits"]) < cost:
                raise HTTPException(status_code=402, detail=f"Private help requires {cost:g} credits in your account.")
            answer = await research(question)
            try:
                updated = await asyncio.to_thread(
                    db.record_user_usage, user_id, credit_cost=cost,
                    idempotency_key=f"user-help:{uuid4().hex}",
                )
            except Exception as error:
                raise HTTPException(status_code=503, detail="Could not settle help credits. Please try again.") from error
            auth_session_cache.invalidate_user(user_id)
            return HelpAnswer(text=answer, html=render_markdown(answer, open_in_new_tab=True),
                              credits_charged=cost, credits=float(updated["credits"]))

    # Limit free research by connection address and total concurrent requests.
    client_key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    expired = [key for key, started in _public_requests.items() if now - started >= _PUBLIC_COOLDOWN_SECONDS]
    for key in expired:
        del _public_requests[key]
    if client_key in _public_requests or client_key in _active_public_requests or len(_active_public_requests) >= 3:
        raise HTTPException(status_code=429, detail="Please wait a moment before asking another question.",
                            headers={"Retry-After": "15"})
    _public_requests[client_key] = now
    _active_public_requests.add(client_key)
    try:
        answer = await research(question)
        return HelpAnswer(text=answer, html=render_markdown(answer, open_in_new_tab=True))
    finally:
        _active_public_requests.discard(client_key)
