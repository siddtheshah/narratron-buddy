"""Free indexed help and opt-in paid personalized help using the same service."""

import asyncio
import os
from uuid import uuid4

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, Field
from typing import Literal

from api_server.dependencies import pricing_controller
from api_server.shared import app, db, get_current_user_async, _require_canvas_access_async
from services.generation_billing import billing_locks
from services.user_help_catalog import UserHelpCatalog
from services.user_help_limits import HelpRateLimitError, UserHelpLimits
from services.user_help_service import DEFAULT_USER_HELP_MODEL, HelpUnavailableError, UserHelpService
from utils.auth_cache import auth_session_cache
from utils.markdown import render_markdown

help_limits = UserHelpLimits()
basic_limits = UserHelpLimits()
basic_limits.cooldown_seconds = 0
basic_limits.hourly_principal_limit = 120
basic_limits.hourly_address_limit = 300
basic_limits.hourly_total_limit = 3000
basic_limits.max_concurrent = 16
help_catalog = UserHelpCatalog(pricing=pricing_controller)


class HelpQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    personalized: bool = False
    quoted_credit_cost: float | None = Field(default=None, ge=0)


class HelpAnswer(BaseModel):
    author: str = "Narratron User Help"
    type: str = "user_help"
    text: str
    html: str
    credits_charged: float = 0.0
    credits: float | None = None
    help_mode: Literal["basic", "personalized"] = "basic"
    can_personalize: bool = False
    personalized_credit_cost: float = 0.0


def help_cost() -> float:
    return float(pricing_controller.get_rates()["live_agent_tool_call_credit_rate"])


async def research(question: str) -> str:
    service = UserHelpService(model=os.environ.get("USER_HELP_MODEL", DEFAULT_USER_HELP_MODEL))
    try:
        return await service.answer(question)
    except HelpUnavailableError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/api/user-help")
def user_help_info(response: Response) -> dict[str, float | bool]:
    response.headers["Cache-Control"] = "no-store"
    return {"basic_free": True, "personalized_credit_cost": help_cost()}


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
        await _require_canvas_access_async(request, theater_id.strip(), join_key=join_key)

    user = await get_current_user_async(request)
    address = f"ip:{request.client.host if request.client else 'unknown'}"
    principal = f"user:{int(user['id'])}" if user else address
    cost = help_cost()
    try:
        if not body.personalized:
            with basic_limits.claim(principal, address):
                answer = await asyncio.to_thread(help_catalog.answer, question)
                return HelpAnswer(text=answer, html=render_markdown(answer, open_in_new_tab=True),
                                  can_personalize=bool(user), personalized_credit_cost=cost)
        if not user:
            raise HTTPException(status_code=401, detail="Sign in to request personalized help.")
        if body.quoted_credit_cost != cost:
            raise HTTPException(status_code=409, detail="The personalized help price changed. Refresh the price before sending.")
        user_id = int(user["id"])
        async with billing_locks.setdefault(user_id, asyncio.Lock()):
            account = await asyncio.to_thread(db.get_user_by_id, user_id)
            if not account or float(account["credits"]) < cost:
                raise HTTPException(status_code=402, detail=f"Personalized help requires {cost:g} credits in your account.")
            with help_limits.claim(principal, address):
                answer = await research(question)
                rendered = render_markdown(answer, open_in_new_tab=True)
                try:
                    updated = await asyncio.to_thread(db.record_user_usage, user_id, credit_cost=cost,
                                                      idempotency_key=f"user-help:{uuid4().hex}")
                except Exception as error:
                    raise HTTPException(status_code=503, detail="Could not settle help credits. Please try again.") from error
                auth_session_cache.invalidate_user(user_id)
                return HelpAnswer(text=answer, html=rendered, help_mode="personalized",
                                  credits_charged=cost, credits=float(updated["credits"]),
                                  personalized_credit_cost=cost)
    except HelpRateLimitError as error:
        raise HTTPException(status_code=429, detail=str(error),
                            headers={"Retry-After": str(error.retry_after)}) from error
