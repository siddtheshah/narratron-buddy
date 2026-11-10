"""Free indexed help and opt-in paid personalized help using the same service."""

import asyncio
import logging
import os
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, Field, computed_field
from typing import Literal

from api_server.dependencies import pricing_controller
from api_server.shared import app, db, get_current_user_async, _require_canvas_access_async
from services.generation_billing import billing_locks
from services.user_help_catalog import UserHelpCatalog
from services.user_help_limits import HelpRateLimitError, UserHelpLimits
from services.user_help_service import DEFAULT_USER_HELP_MODEL, HelpUnavailableError, UserHelpService
from utils.auth_cache import auth_session_cache
from utils.markdown import render_markdown
from services.user_help_settings import FeatureChange, SettingsProposal, config_digest, explicit_feature_request, update_settings

logger = logging.getLogger(__name__)

help_limits = UserHelpLimits()
basic_limits = UserHelpLimits()
basic_limits.cooldown_seconds = 0
basic_limits.hourly_principal_limit = 120
basic_limits.hourly_address_limit = 300
basic_limits.hourly_total_limit = 3000
basic_limits.max_concurrent = 16
help_catalog = UserHelpCatalog(pricing=pricing_controller)


def require_settings_confirmation(request: Request) -> None:
    """Require an explicit same-origin JSON action, including for cookie auth."""
    if request.headers.get("X-Narratron-Settings-Confirm") != "1":
        raise HTTPException(403, "Confirm settings changes from the theater's canvas.")
    if request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "Settings changes require a JSON request.")
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        raise HTTPException(403, "Cross-site settings changes are not allowed.")
    origin = request.headers.get("Origin")
    if origin is not None:
        expected = urlsplit(str(request.base_url))
        try:
            supplied = urlsplit(origin)
        except ValueError:
            raise HTTPException(403, "Invalid request origin.") from None
        if (supplied.scheme, supplied.netloc) != (expected.scheme, expected.netloc) or supplied.path or supplied.query or supplied.fragment:
            raise HTTPException(403, "Cross-origin settings changes are not allowed.")


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
    settings_proposal: SettingsProposal | None = None

    @computed_field
    @property
    def settings_summary(self) -> list[str]:
        return self.settings_proposal.summaries() if self.settings_proposal else []


def help_cost() -> float:
    return float(pricing_controller.get_rates()["live_agent_tool_call_credit_rate"])


async def research(question: str, changes: list[FeatureChange] | None = None) -> str:
    service = UserHelpService(model=os.environ.get("USER_HELP_MODEL", DEFAULT_USER_HELP_MODEL),
                              allow_settings_proposals=changes is not None)
    try:
        answer = await service.answer(question)
        if changes is not None:
            changes.extend(service.settings_changes)
        return answer
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
        theater_id = theater_id.strip()
        await _require_canvas_access_async(request, theater_id, join_key=join_key)

    user = await get_current_user_async(request)
    address = f"ip:{request.client.host if request.client else 'unknown'}"
    principal = f"user:{int(user['id'])}" if user else address
    cost = help_cost()
    snapshot: bytes | None = None
    if theater_id and user:
        from api_server.shared import theater_manager

        deployment = await asyncio.to_thread(db.get_deployment, theater_id)
        if deployment and deployment["user_id"] == int(user["id"]):
            path = theater_manager.theater(theater_id).directory() / "theater.yaml"
            if path.is_file():
                snapshot = await asyncio.to_thread(path.read_bytes)

    def proposal(changes: list[FeatureChange]) -> SettingsProposal | None:
        if snapshot is None or not changes:
            return None
        return SettingsProposal(config_digest=config_digest(snapshot), changes=changes)

    try:
        if not body.personalized:
            with basic_limits.claim(principal, address):
                settings_proposal = proposal(explicit_feature_request(question))
                answer = await asyncio.to_thread(help_catalog.answer, question)
                if settings_proposal is not None:
                    answer = ("Review the proposed feature changes below, then choose **Apply settings and restart agent**. "
                              "You must hold the baton to apply them.\n\n"
                              "See the [theater settings reference](/docs/theater-yaml) for details.")
                return HelpAnswer(text=answer, html=render_markdown(answer, open_in_new_tab=True),
                                  can_personalize=bool(user), personalized_credit_cost=cost,
                                  settings_proposal=settings_proposal)
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
                changes: list[FeatureChange] = []
                answer = await research(question, changes) if snapshot is not None else await research(question)
                rendered = render_markdown(answer, open_in_new_tab=True)
                try:
                    updated = await asyncio.to_thread(db.record_user_usage, user_id, credit_cost=cost,
                                                      idempotency_key=f"user-help:{uuid4().hex}")
                except Exception as error:
                    raise HTTPException(status_code=503, detail="Could not settle help credits. Please try again.") from error
                auth_session_cache.invalidate_user(user_id)
                return HelpAnswer(text=answer, html=rendered, help_mode="personalized",
                                  credits_charged=cost, credits=float(updated["credits"]),
                                  personalized_credit_cost=cost, settings_proposal=proposal(changes))
    except HelpRateLimitError as error:
        raise HTTPException(status_code=429, detail=str(error),
                            headers={"Retry-After": str(error.retry_after)}) from error


@app.post("/api/user-help/{theater_id}/apply-settings")
async def apply_help_settings(theater_id: str, body: SettingsProposal, request: Request) -> dict[str, str | bool]:
    """Apply reviewed switches through the shared checkout lifecycle and restart."""
    from api_server.dependencies import FLAGS, canvas_states, live_agent_manager
    from api_server.shared import theater_manager, theater_repository
    from api_server.theater_editor import invalid_input, require_user, store, write_checkout
    from services.theater_builder import DraftInfo, safe_asset_path
    from services.theater_checkout import checkout_locks, save_theater_checkout

    owner_id = await require_user(request)
    require_settings_confirmation(request)
    async with checkout_locks.setdefault(theater_id, asyncio.Lock()):
        deployment = await asyncio.to_thread(db.get_deployment, theater_id)
        if not deployment:
            raise HTTPException(404, "Theater not found.")
        if deployment["user_id"] != owner_id:
            raise HTTPException(403, "Only the theater owner can apply settings.")
        if (deployment.get("active_orator_id") or owner_id) != owner_id:
            raise HTTPException(409, "Take back the baton before applying settings.")
        account = await asyncio.to_thread(db.get_user_by_id, owner_id)
        if not account or float(account["credits"]) <= 0:
            raise HTTPException(402, "Add credits before applying settings and restarting the agent.")
        with invalid_input():
            builder = store()
            path = safe_asset_path(theater_manager.theater(theater_id).directory(), "theater.yaml")
            if not path.is_file():
                raise HTTPException(404, "Theater configuration not found.")
            content = await asyncio.to_thread(path.read_bytes)
            if config_digest(content) != body.config_digest:
                raise HTTPException(409, "Settings changed since this answer. Ask for help again to review a fresh proposal.")
            updated = update_settings(content, body.changes)
            info = await asyncio.to_thread(builder.load, theater_id)
            if info is None:
                metadata = theater_manager.get_theater(theater_id)
                info = DraftInfo(theater_id=theater_id, owner_id=owner_id, name=metadata.name)
            if info.owner_id != owner_id:
                raise HTTPException(403, "Only the theater owner can apply settings.")
            await asyncio.to_thread(write_checkout, info, {"theater.yaml": updated})
        canvas_states.persist(theater_id)
        if not await asyncio.to_thread(save_theater_checkout, theater_id, theater_manager, theater_repository):
            return {"applied": True, "agent_running": False,
                    "message": "Settings updated in the working copy, but saving failed. Save the theater and start the agent from its controls."}
        try:
            session = live_agent_manager.get_or_create_session(
                theater_id=theater_id, canvas_state_service=canvas_states,
                use_in_memory_artifacts=FLAGS.use_in_memory_artifacts,
            )
            session.summon()
        except Exception:
            logger.exception("Could not restart agent after applying help settings for %s", theater_id)
            return {"applied": True, "agent_running": False,
                    "message": "Settings saved, but the agent could not restart. Start it from the agent controls."}
        return {"applied": True, "agent_running": True, "message": "Settings saved and agent restarted."}
