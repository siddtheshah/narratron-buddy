"""One durable ADK session with application-owned, role-specific model views."""

import asyncio
from collections.abc import AsyncGenerator
import json
from pathlib import Path
from threading import RLock, Thread
from typing import Literal
from uuid import uuid4

from google.adk.agents import Agent, BaseAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.apps import App
from google.adk.events import Event
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.adk.sessions.session import Session
from google.genai import types
from pydantic import JsonValue, TypeAdapter


StoryRole = Literal["responder", "recorder", "planner"]
APP_NAME = "narratron_story"
USER_ID = "story"
ROLE_AUTHORS = {
    "responder": "story_responder",
    "recorder": "story_recorder",
    "planner": "story_deep_planner",
}


def project_role_contents(events: list[Event], role: StoryRole) -> list[types.Content]:
    """Keep entire own-role exchanges in order, including their tool call IDs.

    Cross-role handoffs are application-built inputs in the recipient's invocation.
    Unlabelled ingress, peer events, and session-wide summaries are never replayed.
    """
    permitted = [
        event for event in events
        if event.invocation_id.startswith(f"{role}:")
        and event.author in ("user", ROLE_AUTHORS[role])
        and event.content is not None
        and event.content.parts
        and not event.partial
    ]
    exchanges: dict[str, list[types.Content]] = {}
    for event in permitted:
        assert event.content is not None
        exchanges.setdefault(event.invocation_id, []).append(event.content)
    complete: set[str] = set()
    for invocation_id, contents in exchanges.items():
        pending: list[tuple[str, str]] = []
        valid = True
        for content in contents:
            for part in content.parts or []:
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
        if valid and not pending:
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
    llm_request.contents = project_role_contents(callback_context.session.events, role)
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
                raise ValueError("Story session disappeared during an invocation.")
            result = await super().append_event(latest, event)
            session.events = latest.events
            session.state.update(latest.state)
            session.last_update_time = latest.last_update_time
            return result


class StorySession:
    """Coordinate shared ADK writes and keep its database on one event loop.

    The full history is persisted; projections never edit that history. Automatic
    mixed-history compaction and provider compression are deliberately disabled.
    """

    def __init__(self, path: Path, session_id: str) -> None:
        self.session_id = session_id
        self.session_service = StorySessionService(path)
        self._lock = RLock()
        self._loop = asyncio.new_event_loop()
        self._thread: Thread | None = None
        self.runner: Runner | None = None
        self._closed = False

    def bind(self, agents: list[Agent]) -> None:
        for agent in agents:
            agent.include_contents = "none"
            agent.before_model_callback = build_role_request
        self.runner = Runner(
            app=App(name=APP_NAME, root_agent=StoryRoleRouter(name="story", sub_agents=agents)),
            session_service=self.session_service,
            auto_create_session=True,
        )

    def run(self, role: StoryRole, prompt: str, *, timeout: float = 60.0, stage_id: str = "") -> str:
        with self._lock:
            if self._closed:
                raise RuntimeError("Story session is closed.")
            if self._thread is None:
                self._thread = Thread(target=self._loop.run_forever, daemon=True, name="story-adk")
                self._thread.start()
            future = asyncio.run_coroutine_threadsafe(
                asyncio.wait_for(self._run(role, prompt, stage_id), timeout), self._loop,
            )
        return future.result(timeout=timeout + 5.0)

    async def _run(self, role: StoryRole, prompt: str, stage_id: str) -> str:
        if self.runner is None:
            raise RuntimeError("Story roles have not been bound.")
        final_text = ""
        structured_args: dict[str, JsonValue] | None = None
        async for event in self.runner.run_async(
            user_id=USER_ID,
            session_id=self.session_id,
            invocation_id=f"{role}:{stage_id}:{uuid4().hex}",
            new_message=types.Content(role="user", parts=[types.Part(text=prompt)]),
        ):
            if event.author == ROLE_AUTHORS[role] and event.content:
                for part in event.content.parts or []:
                    if part.function_call is not None and part.function_call.name == "set_model_response":
                        structured_args = TypeAdapter(dict[str, JsonValue]).validate_python(
                            dict(part.function_call.args or {})
                        )
                if event.is_final_response():
                    final_text = "".join(part.text or "" for part in event.content.parts or [])
        if not final_text.strip():
            raise ValueError(f"Story {role} returned no completion.")
        if structured_args is not None:
            return json.dumps(structured_args, ensure_ascii=False)
        return final_text

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._thread is not None:
                asyncio.run_coroutine_threadsafe(self.session_service.close(), self._loop).result(5)
                self._loop.call_soon_threadsafe(self._loop.stop)
                self._thread.join(timeout=5)
            self._loop.close()
