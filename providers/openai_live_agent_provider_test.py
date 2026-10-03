"""Test the provider with the real ADK runner and a mocked OpenAI socket."""

import asyncio
from contextlib import aclosing
import json
from unittest.mock import AsyncMock, MagicMock, patch

from google.adk.agents import Agent
from google.adk.models.llm_request import LlmRequest
from google.adk.runners import InMemoryRunner
from google.genai import types
from openai.types.realtime.realtime_session_create_request import (
    RealtimeSessionCreateRequest,
)
from pydantic import JsonValue
import pytest

from providers import (
    LiveAgentConfig,
    LiveAgentProviderError,
    LiveAgentRunRequest,
    get_live_agent_provider,
)
from providers.live_agent_provider import OpenAIRealtimeConfig
from providers.openai_live_agent_provider import (
    OpenAILiveAgentProvider,
    build_openai_session_config,
)
from providers.openai_realtime_connection_test import make_socket, response_event
from tools.tool_metadata import annotated_function_tool, terminal
from services.priority_live_request_queue import PriorityLiveRequestQueue


def test_session_configuration_uses_ga_schema_text_and_manual_vad() -> None:
    request = LlmRequest(
        config=types.GenerateContentConfig(
            system_instruction="Use tools to narrate.",
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name="play_music",
                            description="Play a track",
                            parameters=types.Schema(
                                type=types.Type.OBJECT,
                                required=["track"],
                                properties={
                                    "track": types.Schema(type=types.Type.STRING),
                                    "volume": types.Schema(
                                        type=types.Type.NUMBER,
                                        minimum=0,
                                        maximum=1,
                                        nullable=True,
                                    ),
                                },
                            ),
                        )
                    ]
                )
            ],
        )
    )
    session = build_openai_session_config(
        request, "gpt-realtime-2.1-mini", OpenAIRealtimeConfig()
    )
    validated = RealtimeSessionCreateRequest.model_validate(session)
    assert validated.output_modalities == ["text"]
    assert session["audio"]["input"]["turn_detection"] is None
    assert "transcription" not in session["audio"]["input"]
    assert "output" not in session["audio"]
    assert session["max_output_tokens"] == 1024
    assert "When a [Story Planner Result] arrives" in session["instructions"]
    assert "Never invent player" in session["instructions"]
    assert session["truncation"]["token_limits"]["post_instructions"] == 8000
    parameters = session["tools"][0]["parameters"]
    assert parameters["type"] == "object"
    assert parameters["properties"]["track"]["type"] == "string"
    assert parameters["properties"]["volume"]["type"] == ["number", "null"]
    assert parameters["properties"]["volume"]["maximum"] == 1


def test_json_schema_tools_and_optional_transcripts() -> None:
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
    }
    request = LlmRequest(
        config=types.GenerateContentConfig(
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name="chat", parameters_json_schema=schema
                        )
                    ],
                )
            ]
        )
    )
    session = build_openai_session_config(
        request,
        "custom",
        OpenAIRealtimeConfig(input_transcription_model="gpt-4o-mini-transcribe"),
    )
    assert session["tools"][0]["parameters"] == schema
    assert (
        session["audio"]["input"]["transcription"]["model"] == "gpt-4o-mini-transcribe"
    )


def test_registry_and_run_config_do_not_require_credentials() -> None:
    provider = get_live_agent_provider("openai")
    config = LiveAgentConfig(provider="openai", max_tool_workers=4)
    with patch.dict("os.environ", {}, clear=True):
        assert provider.create_model(config).model == "gpt-realtime-2.1"
    run_config = provider.build_run_config(config)
    assert run_config.response_modalities == ["TEXT"]
    assert run_config.input_audio_transcription is None
    assert run_config.output_audio_transcription is None
    assert run_config.session_resumption is None
    assert run_config.context_window_compression is None
    assert run_config.tool_thread_pool_config.max_workers == 4
    assert not provider.requires_tool_reminders
    assert provider.background_content_is_partial


def test_deferred_tools_are_explained_in_session_instructions() -> None:
    @terminal
    def show_image() -> str:
        """Display a scene."""
        return "Successfully displayed the scene."

    def browse_images() -> list[str]:
        """Find a scene to display."""
        return ["forest"]

    request = LlmRequest(config=types.GenerateContentConfig(system_instruction="Stage scenes."))
    request.append_tools([annotated_function_tool(show_image), annotated_function_tool(browse_images)])
    session = build_openai_session_config(
        request,
        "gpt-realtime-2.1",
        OpenAIRealtimeConfig(),
    )
    assert "may not trigger another response: show_image." in session["instructions"]
    assert "Request all independent staging actions together" in session["instructions"]
    assert {tool["name"] for tool in session["tools"]} == {"show_image", "browse_images"}
    assert all("terminal" not in tool for tool in session["tools"])


@pytest.mark.asyncio
async def test_missing_key_fails_explicitly_before_connecting() -> None:
    model = OpenAILiveAgentProvider().create_model(LiveAgentConfig(provider="openai"))
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("providers.openai_live_agent_provider.AsyncOpenAI") as client,
    ):
        with pytest.raises(LiveAgentProviderError, match="OPENAI_API_KEY"):
            async with model.connect(LlmRequest()):
                pytest.fail("must not connect without credentials")
        client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("input_mode", ["text", "audio", "notification"])
@pytest.mark.parametrize("terminal_action", [False, True])
async def test_adk_executes_tool_and_sends_matching_result_before_next_response(
    input_mode: str,
    terminal_action: bool,
) -> None:
    socket = make_socket()
    wire_events: asyncio.Queue[bytes] = asyncio.Queue()
    wire_events.put_nowait(
        json.dumps(
            {
                "type": "session.updated",
                "session": {"id": "s1", "output_modalities": ["text"]},
            }
        ).encode()
    )
    socket.recv_bytes = AsyncMock(side_effect=wire_events.get)
    tool_arguments: list[str] = []

    def report_probe(label: str) -> dict[str, str]:
        tool_arguments.append(label)
        return {"status": "ok", "label": label}

    if terminal_action:
        terminal(report_probe)
    tool_result_delivered = asyncio.Event()

    async def create_item(*, item: dict[str, JsonValue]) -> None:
        if item["type"] == "function_call_output":
            tool_result_delivered.set()

    socket.conversation.item.create = AsyncMock(side_effect=create_item)

    async def create_response() -> None:
        if len(tool_arguments) == 0:
            event = response_event(
                "r1",
                [
                    {
                        "type": "function_call",
                        "call_id": "call_probe",
                        "name": "report_probe",
                        "arguments": '{"label":"typed_input"}',
                    }
                ],
            )
        else:
            event = response_event(
                "r2",
                [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Done"}],
                    }
                ],
            )
        wire_events.put_nowait(json.dumps(event).encode())

    async def response_create(*, response: dict[str, list[str]]) -> None:
        assert response == {"output_modalities": ["text"]}
        await create_response()

    socket.response.create = AsyncMock(side_effect=response_create)
    socket.session = MagicMock()
    socket.session.update = AsyncMock()
    transport_context = MagicMock()
    transport_context.__aenter__ = AsyncMock(return_value=socket)
    transport_context.__aexit__ = AsyncMock(return_value=False)
    client = MagicMock()
    client.realtime.connect.return_value = transport_context
    client_context = MagicMock()
    client_context.__aenter__ = AsyncMock(return_value=client)
    client_context.__aexit__ = AsyncMock(return_value=False)

    provider = OpenAILiveAgentProvider()
    config = LiveAgentConfig(provider="openai")
    agent = Agent(
        name="test_agent",
        model=provider.create_model(config),
        instruction="Call report_probe.",
        tools=[annotated_function_tool(report_probe)],
    )
    runner = InMemoryRunner(agent=agent, app_name="openai_adapter_test")
    session = await runner.session_service.create_session(
        app_name=runner.app_name, user_id="user"
    )
    queue = PriorityLiveRequestQueue(
        live_tool_budget=1,
        background_content_is_partial=True,
        tool_results_bypass_input_window=provider.tool_results_bypass_input_window,
    )
    if input_mode == "text":
        queue.send_user_input(
            types.Content(role="user", parts=[types.Part(text="typed input probe")])
        )
    elif input_mode == "audio":
        queue.send_activity_start()
        queue.send_realtime(
            types.Blob(data=b"\x01\x00" * 1600, mime_type="audio/pcm;rate=16000")
        )
        queue.send_activity_end()
    else:
        queue.send_notification(
            types.Content(
                parts=[types.Part(text="[Story Planner Result] The door opens.")]
            )
        )
    request = LiveAgentRunRequest(
        runner=runner,
        user_id="user",
        session_id=session.id,
        input_queue=queue,
        run_config=provider.build_run_config(config),
    )
    results: list[str] = []

    async def consume() -> None:
        async with aclosing(provider.run_live(request)) as events:
            async for event in events:
                if event.get_function_calls():
                    queue.record_model_tool_calls(len(event.get_function_calls()))
                results.extend(
                    response.id for response in event.get_function_responses()
                )
                if terminal_action and results:
                    await tool_result_delivered.wait()
                    await asyncio.sleep(0)
                    return
                if event.content and any(
                    part.text == "Done" for part in event.content.parts or []
                ):
                    return

    try:
        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
            patch(
                "providers.openai_live_agent_provider.AsyncOpenAI",
                return_value=client_context,
            ),
        ):
            await asyncio.wait_for(consume(), timeout=5)
    finally:
        await runner.close()
    assert tool_arguments == ["typed_input"]
    assert results == ["call_probe"]
    outputs = [
        call.kwargs["item"]
        for call in socket.conversation.item.create.call_args_list
        if call.kwargs["item"]["type"] == "function_call_output"
    ]
    assert len(outputs) == 1
    assert outputs[0]["call_id"] == "call_probe"
    assert json.loads(outputs[0]["output"])["label"] == "typed_input"
    assert socket.response.create.await_count == (1 if terminal_action else 2)
    assert socket.input_audio_buffer.commit.await_count == (
        1 if input_mode == "audio" else 0
    )
    socket.close.assert_awaited_once()
    sent_session = socket.session.update.call_args.kwargs["session"]
    assert sent_session["instructions"].startswith("Call report_probe.")
    assert sent_session["tools"][0]["name"] == "report_probe"


@pytest.mark.asyncio
async def test_setup_rejection_closes_socket_and_client() -> None:
    socket = make_socket(
        [{"type": "error", "error": {"code": "invalid", "message": "bad config"}}]
    )
    socket.session = MagicMock(update=AsyncMock())
    transport_context = MagicMock()
    transport_context.__aenter__ = AsyncMock(return_value=socket)
    transport_context.__aexit__ = AsyncMock(return_value=False)
    client_context = MagicMock()
    client_context.__aenter__ = AsyncMock(return_value=MagicMock())
    client_context.__aenter__.return_value.realtime.connect.return_value = (
        transport_context
    )
    client_context.__aexit__ = AsyncMock(return_value=False)
    model = OpenAILiveAgentProvider().create_model(LiveAgentConfig())
    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
        patch(
            "providers.openai_live_agent_provider.AsyncOpenAI",
            return_value=client_context,
        ),
        pytest.raises(LiveAgentProviderError, match="bad config"),
    ):
        async with model.connect(LlmRequest()):
            pytest.fail("setup should fail")
    socket.close.assert_awaited_once()
    client_context.__aexit__.assert_awaited_once()
