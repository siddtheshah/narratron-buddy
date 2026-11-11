"""One durable ADK session with application-owned, role-specific model views."""

import asyncio
from collections.abc import AsyncGenerator
from collections.abc import Callable
import json
import logging
from pathlib import Path
from threading import RLock, Thread
import time
from typing import Literal
from uuid import uuid4

from google.adk.agents import Agent, BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.apps import App
from google.adk.events import Event
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins import ReflectAndRetryToolPlugin
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.adk.sessions.session import Session
from google.genai import types
from pydantic import JsonValue, TypeAdapter


logger = logging.getLogger(__name__)

StoryRole = Literal["responder", "game", "recorder", "planner"]
APP_NAME = "narratron_story"
USER_ID = "story"
ROLE_AUTHORS = {
    "responder": "story_responder",
    "game": "story_game",
    "recorder": "story_recorder",
    "planner": "story_deep_planner",
}


def project_role_contents(
    events: list[Event], role: StoryRole, invocation_id: str | None = None,
) -> list[types.Content]:
    """Keep only the active invocation, including its complete tool exchanges.

    Prior invocations, peer events, and session summaries are never replayed.
    Historical context comes from the unified story journal's role view.
    """
    role_events = [
        event for event in events
        if event.invocation_id.startswith(f"{role}:")
    ]
    if invocation_id is None:
        invocation_id = role_events[-1].invocation_id if role_events else ""
    role_events = [event for event in role_events if event.invocation_id == invocation_id]
    active_invocation_id = role_events[-1].invocation_id if role_events else ""
    failed_invocations = {
        event.invocation_id for event in role_events
        if event.error_code or event.finish_reason == types.FinishReason.MALFORMED_FUNCTION_CALL
    }
    permitted = [
        event for event in role_events
        if event.author in ("user", ROLE_AUTHORS[role])
        and event.content is not None
        and event.content.parts
        and not event.partial
    ]
    exchanges: dict[str, list[Event]] = {}
    for event in permitted:
        exchanges.setdefault(event.invocation_id, []).append(event)
    complete: set[str] = set()
    for invocation_id, inv_events in exchanges.items():
        if invocation_id in failed_invocations:
            continue
        pending: list[tuple[str, str]] = []
        valid = True
        for event in inv_events:
            assert event.content is not None
            for part in event.content.parts or []:
                if part.function_call is not None:
                    call = part.function_call
                    pending.append((call.id or "", call.name or ""))
                elif part.function_response is not None:
                    response = part.function_response
                    key = (response.id or "", response.name or "")
                    if key not in pending:
                        valid = False
                        break
                    pending.remove(key)
        finished = (
            inv_events[-1].author == ROLE_AUTHORS[role]
            and inv_events[-1].is_final_response()
        )
        if valid and not pending and (finished or invocation_id == active_invocation_id):
            complete.add(invocation_id)
    return [
        event.content.model_copy(deep=True) for event in permitted
        if event.invocation_id in complete and event.content is not None
    ]


def build_role_request(
    callback_context: CallbackContext, llm_request: LlmRequest,
) -> LlmResponse | None:
    """Replace ADK's current-turn content too, on every model continuation."""
    role = TypeAdapter(StoryRole).validate_python(callback_context.invocation_id.split(":", 1)[0])
    llm_request.contents = project_role_contents(
        callback_context.session.events, role, callback_context.invocation_id,
    )
    return None


class StoryRoleRouter(BaseAgent):
    """Dispatch supplied by the orchestrator, never a model transfer decision."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        role = ctx.invocation_id.split(":", 1)[0]
        author = ROLE_AUTHORS[role]
        agent = next(agent for agent in self.sub_agents if agent.name == author)
        async for event in agent.run_async(ctx):
            yield event


class StorySessionService(DatabaseSessionService):
    """Coordinate appends from concurrent roles against the latest stored version.

    Models retain their original role inputs. Only distinct, application-scoped
    output keys are merged into shared state; this never changes a model view.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(f"sqlite+aiosqlite:///{path.as_posix()}")
        self._append_lock = asyncio.Lock()

    async def append_event(self, session: Session, event: Event) -> Event:
        if event.partial:
            return event
        async with self._append_lock:
            latest = await self.get_session(app_name=session.app_name, user_id=session.user_id, session_id=session.id)
            if latest is None:
                logger.error(
                    "[StorySessionService] Story session disappeared during invocation (session_id=%s, invocation_id=%s)",
                    session.id,
                    event.invocation_id,
                )
                raise ValueError("Story session disappeared during an invocation.")
            # ADK can construct a model event before its before-model callbacks.
            # Preserve append order when a callback inserts new planner input.
            event.timestamp = max(time.time(), latest.events[-1].timestamp + 0.000001 if latest.events else 0.0)
            result = await super().append_event(latest, event)
            session.events = latest.events
            session.state.update(latest.state)
            session.last_update_time = latest.last_update_time
            logger.debug(
                "[StorySessionService] Appended event author=%s invocation=%s final=%s error=%s finish=%s",
                event.author,
                event.invocation_id,
                event.is_final_response(),
                event.error_code,
                event.finish_reason,
            )
            return result


class StorySession:
    """Coordinate shared ADK writes and keep its database on one event loop.

    Full history remains an audit trail, never replayed into model requests.
    Models see their active invocation and a frozen, chunked journal context.
    """

    def __init__(self, path: Path, session_id: str, *, max_attempts: int = 2) -> None:
        self.session_id = session_id
        self.session_service = StorySessionService(path)
        self.max_attempts = max(1, max_attempts)
        self._lock = RLock()
        self._loop = asyncio.new_event_loop()
        self._thread: Thread | None = None
        self.runner: Runner | None = None
        self._closed = False
        self.planner_context: Callable[[], str] | None = None
        self.history_context: Callable[[StoryRole], str] | None = None
        self._history_snapshots: dict[str, str] = {}
        self._creation_lock = asyncio.Lock()

    async def _build_role_request(
        self, callback_context: CallbackContext, llm_request: LlmRequest,
    ) -> LlmResponse | None:
        if callback_context.invocation_id.startswith("planner:") and self.planner_context is not None:
            prompt = self.planner_context()
            if prompt:
                logger.debug(
                    "[StorySession] Injecting mid-invocation planner_context (len=%d) for %s",
                    len(prompt),
                    callback_context.invocation_id,
                )
                await self.session_service.append_event(callback_context.session, Event(
                    author="user", invocation_id=callback_context.invocation_id,
                    content=types.Content(role="user", parts=[types.Part(text=prompt)]),
                ))
        result = build_role_request(callback_context, llm_request)
        if self.history_context is not None:
            invocation_id = callback_context.invocation_id
            if invocation_id not in self._history_snapshots:
                role = TypeAdapter(StoryRole).validate_python(invocation_id.split(":", 1)[0])
                self._history_snapshots[invocation_id] = self.history_context(role)
            prompt = self._history_snapshots[invocation_id]
            if prompt:
                llm_request.contents.insert(0, types.Content(role="user", parts=[types.Part(text=prompt)]))
        logger.debug(
            "[StorySession] Built role request for %s (contents=%d, has_history=%s)",
            callback_context.invocation_id,
            len(llm_request.contents or []),
            callback_context.invocation_id in self._history_snapshots,
        )
        return result

    def bind(self, agents: list[Agent]) -> None:
        for agent in agents:
            agent.include_contents = "none"
            agent.before_model_callback = [self._build_role_request, *agent.canonical_before_model_callbacks]
        self.runner = Runner(
            app=App(
                name=APP_NAME,
                root_agent=StoryRoleRouter(name="story", sub_agents=agents),
                plugins=[ReflectAndRetryToolPlugin()],
            ),
            session_service=self.session_service,
            auto_create_session=True,
        )

    def run(self, role: StoryRole, prompt: str, *, timeout: float = 60.0, stage_id: str = "") -> str:
        logger.debug(
            "[StorySession] run() requested (role=%s, stage_id=%s, timeout=%.1fs, prompt_len=%d)",
            role,
            stage_id,
            timeout,
            len(prompt),
        )
        with self._lock:
            if self._closed:
                logger.warning("[StorySession] run() rejected for role=%s: session is closed", role)
                raise RuntimeError("Story session is closed.")
            if self._thread is None:
                logger.debug("[StorySession] Starting background story-adk event loop thread")
                self._thread = Thread(target=self._loop.run_forever, daemon=True, name="story-adk")
                self._thread.start()
            future = asyncio.run_coroutine_threadsafe(
                asyncio.wait_for(self._run(role, prompt, stage_id), timeout), self._loop,
            )
        started = time.perf_counter()
        try:
            result = future.result(timeout=timeout + 5.0)
            logger.debug(
                "[StorySession] run() completed (role=%s, stage_id=%s, elapsed=%.3fs, result_len=%d)",
                role,
                stage_id,
                time.perf_counter() - started,
                len(result),
            )
            return result
        except Exception as exc:
            logger.warning(
                "[StorySession] run() failed (role=%s, stage_id=%s, elapsed=%.3fs): %s",
                role,
                stage_id,
                time.perf_counter() - started,
                exc,
            )
            raise

    async def _run(self, role: StoryRole, prompt: str, stage_id: str) -> str:
        if self.runner is None:
            raise RuntimeError("Story roles have not been bound.")
        async with self._creation_lock:
            session = await self.session_service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=self.session_id)
            if session is None:
                logger.debug("[StorySession] Creating ADK session %s", self.session_id)
                await self.session_service.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=self.session_id)
        for attempt in range(self.max_attempts):
            final_text = ""
            structured_args: dict[str, JsonValue] | None = None
            malformed = False
            invocation_id = f"{role}:{stage_id}:{uuid4().hex}"
            logger.debug(
                "[StorySession] Starting attempt %d/%d (invocation_id=%s)",
                attempt + 1,
                self.max_attempts,
                invocation_id,
            )
            try:
                async for event in self.runner.run_async(
                    user_id=USER_ID,
                    session_id=self.session_id,
                    invocation_id=invocation_id,
                    new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
                ):
                    if (
                        event.error_code == "MALFORMED_FUNCTION_CALL"
                        or event.finish_reason == types.FinishReason.MALFORMED_FUNCTION_CALL
                    ):
                        malformed = True
                        logger.warning(
                            "[StorySession] MALFORMED_FUNCTION_CALL in %s (attempt %d/%d, error_code=%s)",
                            invocation_id,
                            attempt + 1,
                            self.max_attempts,
                            event.error_code,
                        )
                    if event.author == ROLE_AUTHORS[role] and event.content:
                        for part in event.content.parts or []:
                            if part.function_call is not None:
                                logger.debug(
                                    "[StorySession] %s tool call: %s",
                                    invocation_id,
                                    part.function_call.name,
                                )
                            if part.function_call is not None and part.function_call.name == "set_model_response":
                                structured_args = TypeAdapter(dict[str, JsonValue]).validate_python(
                                    dict(part.function_call.args or {})
                                )
                        if event.is_final_response():
                            final_text = "".join(part.text or "" for part in event.content.parts or [])
                            logger.debug(
                                "[StorySession] %s final response received (text_len=%d, has_structured_args=%s)",
                                invocation_id,
                                len(final_text),
                                structured_args is not None,
                            )
            finally:
                self._history_snapshots.pop(invocation_id, None)
            if final_text.strip():
                if structured_args is not None:
                    return json.dumps(structured_args, ensure_ascii=False)
                return final_text
            logger.warning(
                "[StorySession] %s produced empty final text (malformed=%s, attempt %d/%d)",
                invocation_id,
                malformed,
                attempt + 1,
                self.max_attempts,
            )
            if not malformed or attempt + 1 >= self.max_attempts:
                break
        logger.error("[StorySession] Story %s (stage=%s) returned no completion", role, stage_id)
        raise ValueError(f"Story {role} returned no completion.")

    def close(self) -> None:
        logger.debug("[StorySession] Closing StorySession (session_id=%s)", self.session_id)
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._thread is not None:
                asyncio.run_coroutine_threadsafe(self.session_service.close(), self._loop).result(5)
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._thread.join(timeout=5)
            self._loop.close()
        logger.debug("[StorySession] Closed StorySession (session_id=%s)", self.session_id)
