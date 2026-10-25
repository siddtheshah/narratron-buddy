"""Inspect actual ADK model requests across tool continuations and restarts."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from collections.abc import AsyncGenerator
from pathlib import Path
from threading import Event as ThreadEvent

from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import Field, PrivateAttr
from google.adk.events import Event

from components.story.story_handoffs import GameControlDecision
from components.story.story_session import APP_NAME, USER_ID, StorySession, project_role_contents


class CapturingModel(BaseLlm):
    model: str = "capture"
    replies: list[types.Content] = Field(default_factory=list)
    requests: list[LlmRequest] = Field(default_factory=list)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request.model_copy(deep=True))
        yield LlmResponse(content=self.replies.pop(0))


class BlockingModel(CapturingModel):
    _started: ThreadEvent = PrivateAttr(default_factory=ThreadEvent)
    _release: ThreadEvent = PrivateAttr(default_factory=ThreadEvent)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request.model_copy(deep=True))
        self._started.set()
        while not self._release.is_set():
            await asyncio.sleep(0.01)
        yield LlmResponse(content=self.replies.pop(0))


def final(text: str) -> types.Content:
    return types.Content(role="model", parts=[types.Part(text=text)])


def private_tool() -> str:
    """Read private mechanics, only for the recorder."""
    return "PRIVATE RECEIPT revision=42"


def agents(
    recorder: CapturingModel, responder: CapturingModel, planner: CapturingModel,
) -> list[Agent]:
    return [
        Agent(name="story_recorder", model=recorder, tools=[private_tool], output_key="recorder_output"),
        Agent(name="story_responder", model=responder, output_key="responder_output"),
        Agent(name="story_deep_planner", model=planner, output_key="planner_output"),
    ]


def request_text(request: LlmRequest) -> str:
    return str([content.model_dump(mode="json") for content in request.contents])


def test_actual_requests_isolate_roles_and_preserve_tool_exchanges_on_restart(tmp_path: Path) -> None:
    recorder = CapturingModel(replies=[
        types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="private_tool", args={}, id="private-call"))]),
        final("PRIVATE RECORDER OUTPUT"),
    ])
    responder = CapturingModel(replies=[final("Public narration")])
    planner = CapturingModel(replies=[final("PRIVATE PLANNER OUTPUT")])
    path = tmp_path / "sessions.sqlite"
    shared = StorySession(path, "adventure")
    shared.bind(agents(recorder, responder, planner))
    try:
        assert shared.run("recorder", "PRIVATE RAW GAME STATE") == "PRIVATE RECORDER OUTPUT"
        assert shared.run("responder", "Narrative brief: a key changed hands.") == "Public narration"
        assert shared.run("planner", "Recorded outcome: a key changed hands.") == "PRIVATE PLANNER OUTPUT"
        assert len(recorder.requests) == 2
        continuation = recorder.requests[-1]
        calls = [part.function_call for content in continuation.contents for part in content.parts or [] if part.function_call]
        results = [part.function_response for content in continuation.contents for part in content.parts or [] if part.function_response]
        assert len(calls) == len(results) == 1
        assert calls[0].id == results[0].id
        assert calls[0].name == results[0].name == "private_tool"
        response_context = request_text(responder.requests[0])
        assert "Narrative brief" in response_context
        assert "PRIVATE" not in response_context
        assert "private_tool" not in response_context
        assert "PRIVATE RECORDER" not in request_text(planner.requests[0])

        async def stored_event_count() -> int:
            session = await shared.session_service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id="adventure")
            assert session is not None
            assert "PRIVATE RECEIPT" in session.model_dump_json()
            assert "PRIVATE PLANNER OUTPUT" in session.model_dump_json()
            return len(session.events)

        before = asyncio.run_coroutine_threadsafe(stored_event_count(), shared._loop).result(5)
        assert before >= 8
    finally:
        shared.close()

    restarted = StorySession(path, "adventure")
    responder_after_restart = CapturingModel(replies=[final("Next public narration")])
    restarted.bind(agents(CapturingModel(), responder_after_restart, CapturingModel()))
    try:
        restarted.run("responder", "Next communicated action")
        context = request_text(responder_after_restart.requests[0])
        assert "Public narration" in context
        assert "Next communicated action" in context
        assert "PRIVATE" not in context
        assert "private_tool" not in context
    finally:
        restarted.close()


def test_interleaved_roles_coordinate_session_writes_without_mixing_views(tmp_path: Path) -> None:
    recorder = CapturingModel(replies=[final("PRIVATE RECORDER OUTPUT")])
    responder = CapturingModel(replies=[final("Public response")])
    planner = BlockingModel(replies=[final("PRIVATE PLANNER OUTPUT")])
    shared = StorySession(tmp_path / "sessions.sqlite", "interleaved")
    shared.bind(agents(recorder, responder, planner))
    try:
        # Establish the session before starting independent invocations.
        shared.run("recorder", "PRIVATE RAW GAME STATE")
        with ThreadPoolExecutor(max_workers=1) as executor:
            planning = executor.submit(shared.run, "planner", "PRIVATE PLANNING INPUT")
            assert planner._started.wait(5)
            shared.run("responder", "Communicated player action")
            planner._release.set()
            assert planning.result(5) == "PRIVATE PLANNER OUTPUT"
        assert "PRIVATE" not in request_text(responder.requests[0])

        async def verify_storage() -> None:
            session = await shared.session_service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id="interleaved")
            assert session is not None
            assert session.state == {
                "recorder_output": "PRIVATE RECORDER OUTPUT", "responder_output": "Public response",
                "planner_output": "PRIVATE PLANNER OUTPUT",
            }
            assert len(session.events) == 6

        asyncio.run_coroutine_threadsafe(verify_storage(), shared._loop).result(5)
    finally:
        planner._release.set()
        shared.close()


def test_projection_omits_interrupted_unpaired_tool_invocations() -> None:
    events = [
        Event(author="user", invocation_id="responder:failed", content=final("Earlier intention")),
        Event(author="story_responder", invocation_id="responder:failed", content=types.Content(role="model", parts=[
            types.Part(function_call=types.FunctionCall(name="roll_dice", args={}, id="lost-call")),
        ])),
        Event(author="user", invocation_id="responder:retry", content=final("Retry intention")),
    ]
    view = project_role_contents(events, "responder")
    assert len(view) == 1
    assert view[0].parts[0].text == "Retry intention"


def test_planner_receives_new_content_during_tool_continuation_and_persists_it(tmp_path: Path) -> None:
    planner = BlockingModel(replies=[
        types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
            name="private_tool", args={}, id="planner-call",
        ))]),
        final("Updated plan"),
    ])
    responder = CapturingModel(replies=[final("Public response")])
    shared = StorySession(tmp_path / "sessions.sqlite", "live-planner")
    shared.bind([
        Agent(name="story_recorder", model=CapturingModel(), output_key="recorder_output"),
        Agent(name="story_responder", model=responder, output_key="responder_output"),
        Agent(name="story_deep_planner", model=planner, tools=[private_tool], output_key="planner_output"),
    ])
    incoming: list[str] = []

    def context() -> str:
        return incoming.pop(0) if incoming else ""

    shared.planner_context = context
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            planning = executor.submit(shared.run, "planner", "Initial player intention")
            try:
                assert planner._started.wait(5)
                # Simultaneous first invocations must also share session creation.
                shared.run("responder", "New spoken action")
                incoming.append("New responder outcome while planning")
            finally:
                planner._release.set()
            assert planning.result(5) == "Updated plan"
        assert len(planner.requests) == 2
        continuation = request_text(planner.requests[-1])
        assert "Initial player intention" in continuation
        assert "New responder outcome while planning" in continuation
        assert "planner-call" in continuation
        assert "New responder outcome" not in request_text(responder.requests[0])
        planner.replies.append(final("Next plan"))
        shared.run("planner", "Another update")
        assert "New responder outcome while planning" in request_text(planner.requests[-1])
    finally:
        planner._release.set()
        shared.close()


def test_projection_omits_aborted_invocations_after_tool_response_or_malformed_call() -> None:
    events = [
        Event(author="user", invocation_id="recorder:ok", content=types.Content(role="user", parts=[types.Part(text="Turn 1")])),
        Event(author="story_recorder", invocation_id="recorder:ok", content=final("Recorded turn 1")),
        Event(author="user", invocation_id="recorder:malformed", content=types.Content(role="user", parts=[types.Part(text="Turn 2 aborted")])),
        Event(author="story_recorder", invocation_id="recorder:malformed", content=types.Content(role="model", parts=[
            types.Part(function_call=types.FunctionCall(name="check_schema", args={"topic": "Log"}, id="call-1")),
        ])),
        Event(author="story_recorder", invocation_id="recorder:malformed", content=types.Content(role="user", parts=[
            types.Part(function_response=types.FunctionResponse(name="check_schema", response={"result": "ok"}, id="call-1")),
        ])),
        Event(
            author="story_recorder",
            invocation_id="recorder:malformed",
            finish_reason=types.FinishReason.MALFORMED_FUNCTION_CALL,
            error_code="MALFORMED_FUNCTION_CALL",
        ),
        Event(author="user", invocation_id="recorder:retry", content=types.Content(role="user", parts=[types.Part(text="Turn 2 retry")])),
    ]
    view = project_role_contents(events, "recorder")
    assert [part.text for content in view for part in content.parts or []] == [
        "Turn 1",
        "Recorded turn 1",
        "Turn 2 retry",
    ]


class MalformedOnceModel(BaseLlm):
    model: str = "malformed-once"
    requests: list[LlmRequest] = Field(default_factory=list)
    _failed_once: bool = PrivateAttr(default=False)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request.model_copy(deep=True))
        if not self._failed_once:
            self._failed_once = True
            yield LlmResponse(
                error_code="MALFORMED_FUNCTION_CALL",
                finish_reason=types.FinishReason.MALFORMED_FUNCTION_CALL,
            )
            return
        yield LlmResponse(content=final("Recovered recorder output"))


def test_story_session_retries_malformed_function_call_with_clean_view(tmp_path: Path) -> None:
    recorder = MalformedOnceModel()
    shared = StorySession(tmp_path / "sessions.sqlite", "malformed-retry")
    shared.bind([
        Agent(name="story_recorder", model=recorder, tools=[private_tool], output_key="recorder_output"),
        Agent(name="story_responder", model=CapturingModel(), output_key="responder_output"),
        Agent(name="story_deep_planner", model=CapturingModel(), output_key="planner_output"),
    ])
    try:
        assert shared.run("recorder", "Record trade proposal.") == "Recovered recorder output"
        assert len(recorder.requests) == 2
        assert len(recorder.requests[0].contents) == 1
        assert len(recorder.requests[1].contents) == 1
    finally:
        shared.close()


def test_structured_output_tool_preserves_explicit_null_narration_skip(tmp_path: Path) -> None:
    recorder = CapturingModel(replies=[
        types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
            name="set_model_response",
            args={"narration_request": None, "reason": "Routine move.", "factual_updates": {}},
            id="set-response-1",
        ))]),
    ])
    shared = StorySession(tmp_path / "sessions.sqlite", "structured-skip")
    shared.bind([
        Agent(
            name="story_recorder",
            model=recorder,
            tools=[private_tool],
            output_schema=GameControlDecision,
            output_key="recorder_output",
        ),
        Agent(name="story_responder", model=CapturingModel(), output_key="responder_output"),
        Agent(name="story_deep_planner", model=CapturingModel(), output_key="planner_output"),
    ])
    try:
        raw = shared.run("recorder", "Record a minor game move.")
        assert '"narration_request": null' in raw
        decision = GameControlDecision.model_validate_json(raw)
        assert decision.narration_request is None
        assert decision.reason == "Routine move."
    finally:
        shared.close()

