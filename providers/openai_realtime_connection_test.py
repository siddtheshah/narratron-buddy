"""Protocol tests for speech boundaries, serialized turns, and tool delivery."""

import base64
import json
from unittest.mock import AsyncMock, MagicMock

from google.genai import types
from openai.resources.realtime.realtime import AsyncRealtimeConnection
from pydantic import JsonValue
import pytest

from providers.live_agent_provider import LiveAgentProviderError, OpenAIRealtimeConfig
from providers.openai_realtime_connection import OpenAIRealtimeConnection


def make_socket(events: list[dict[str, JsonValue]] | None = None) -> MagicMock:
    socket = MagicMock(spec=AsyncRealtimeConnection)
    socket.input_audio_buffer = MagicMock()
    socket.input_audio_buffer.append = AsyncMock()
    socket.input_audio_buffer.commit = AsyncMock()
    socket.input_audio_buffer.clear = AsyncMock()
    socket.response = MagicMock()
    socket.response.create = AsyncMock()
    socket.conversation = MagicMock()
    socket.conversation.item.create = AsyncMock()
    socket.close = AsyncMock()
    socket.recv_bytes = AsyncMock(
        side_effect=[json.dumps(event).encode() for event in events or []]
    )
    return socket


def response_event(
    response_id: str, output: list[dict[str, JsonValue]]
) -> dict[str, JsonValue]:
    return {
        "type": "response.done",
        "response": {
            "id": response_id,
            "status": "completed",
            "output": output,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 10,
                "total_tokens": 110,
                "input_token_details": {
                    "cached_tokens": 80,
                    "audio_tokens": 20,
                    "text_tokens": 80,
                },
            },
        },
    }


def user_text(text: str = "Go north") -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


@pytest.mark.asyncio
async def test_manual_vad_resamples_audio_and_commits_only_at_speech_end() -> None:
    socket = make_socket()
    connection = OpenAIRealtimeConnection(socket, "test-model", OpenAIRealtimeConfig())
    audio = types.Blob(data=b"\x01\x00" * 1600, mime_type="audio/pcm;rate=16000")
    await connection.send_realtime(audio)  # Idle microphone input is ignored.
    socket.input_audio_buffer.append.assert_not_awaited()
    await connection.send_realtime(types.ActivityStart())
    await connection.send_realtime(audio)
    socket.response.create.assert_not_awaited()
    socket.input_audio_buffer.commit.assert_not_awaited()
    await connection.send_realtime(types.ActivityEnd())
    data = b"".join(
        base64.b64decode(call.kwargs["audio"])
        for call in socket.input_audio_buffer.append.call_args_list
    )
    assert len(data) == 2400 * 2
    socket.input_audio_buffer.commit.assert_awaited_once()
    socket.response.create.assert_awaited_once_with(
        response={"output_modalities": ["text"]}
    )
    await connection.send_realtime(types.ActivityEnd())
    assert socket.input_audio_buffer.commit.await_count == 1


@pytest.mark.asyncio
async def test_empty_or_short_activity_does_not_trigger_model() -> None:
    socket = make_socket()
    connection = OpenAIRealtimeConnection(socket, "test-model", OpenAIRealtimeConfig())
    await connection.send_realtime(types.ActivityStart())
    await connection.send_realtime(types.ActivityEnd())
    await connection.send_realtime(types.ActivityStart())
    await connection.send_realtime(
        types.Blob(data=b"\0\0" * 400, mime_type="audio/pcm;rate=16000")
    )
    await connection.send_realtime(types.ActivityEnd())
    socket.input_audio_buffer.commit.assert_not_awaited()
    socket.response.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_resampling_is_continuous_across_microphone_chunks() -> None:
    async def resample(chunks: list[bytes]) -> bytes:
        socket = make_socket()
        connection = OpenAIRealtimeConnection(
            socket, "test-model", OpenAIRealtimeConfig()
        )
        await connection.send_realtime(types.ActivityStart())
        for chunk in chunks:
            await connection.send_realtime(
                types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
            )
        await connection.send_realtime(types.ActivityEnd())
        return b"".join(
            base64.b64decode(call.kwargs["audio"])
            for call in socket.input_audio_buffer.append.call_args_list
        )

    audio = bytes(range(256)) * 50
    assert await resample([audio]) == await resample(
        [audio[offset : offset + 512] for offset in range(0, len(audio), 512)]
    )


@pytest.mark.asyncio
async def test_background_context_does_not_generate_response() -> None:
    socket = make_socket()
    connection = OpenAIRealtimeConnection(socket, "test-model", OpenAIRealtimeConfig())
    await connection._send_content(user_text("Current canvas: forest"), partial=True)
    socket.conversation.item.create.assert_awaited_once()
    socket.response.create.assert_not_awaited()
    await connection.send_content(user_text())
    socket.response.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_waits_for_all_parallel_tool_results_and_preserves_ids() -> None:
    socket = make_socket(
        [
            response_event(
                "r1",
                [
                    {
                        "type": "function_call",
                        "call_id": "call_a",
                        "name": "play_music",
                        "arguments": '{"music_id":"forest"}',
                    },
                    {
                        "type": "function_call",
                        "call_id": "call_b",
                        "name": "send_chat_message",
                        "arguments": '{"message":"Hello"}',
                    },
                ],
            )
        ]
    )
    connection = OpenAIRealtimeConnection(socket, "test-model", OpenAIRealtimeConfig())
    await connection.send_content(user_text())
    responses = [response async for response in connection.receive()]
    calls = responses[0].get_function_calls()
    assert [call.id for call in calls] == ["call_a", "call_b"]
    assert calls[0].args == {"music_id": "forest"}
    assert responses[-1].usage_metadata.cached_content_token_count == 80
    for call in calls:
        await connection._send_content(
            types.Content(
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=call.id,
                            name=call.name,
                            response={"status": "ok"},
                        )
                    )
                ]
            ),
            partial=True,
        )
        if call.id == "call_a":
            assert socket.response.create.await_count == 1
    assert socket.response.create.await_count == 2
    assert (
        socket.conversation.item.create.call_args.kwargs["item"]["call_id"] == "call_b"
    )


@pytest.mark.asyncio
async def test_new_user_input_waits_for_active_response_and_speech_end() -> None:
    socket = make_socket([response_event("r1", [])])
    connection = OpenAIRealtimeConnection(socket, "test-model", OpenAIRealtimeConfig())
    await connection.send_content(user_text())
    await connection.send_realtime(types.ActivityStart())
    await connection.send_content(user_text("Look around"))
    assert socket.response.create.await_count == 1
    responses = [response async for response in connection.receive()]
    assert responses[-1].turn_complete
    assert socket.response.create.await_count == 1
    await connection.send_realtime(types.ActivityEnd())
    assert socket.response.create.await_count == 2


@pytest.mark.asyncio
async def test_tool_continuations_are_bounded_until_next_user_input() -> None:
    socket = make_socket(
        [
            response_event(
                "r1",
                [
                    {
                        "type": "function_call",
                        "call_id": "call_a",
                        "name": "tool",
                        "arguments": "{}",
                    },
                ],
            )
        ]
    )
    connection = OpenAIRealtimeConnection(
        socket, "test-model", OpenAIRealtimeConfig(max_response_turns=1)
    )
    await connection.send_content(user_text())
    responses = [response async for response in connection.receive()]
    assert responses[0].get_function_calls()
    await connection.send_content(
        types.Content(
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="call_a",
                        name="tool",
                        response={"status": "ok"},
                    )
                )
            ]
        )
    )
    assert socket.response.create.await_count == 1
    await connection._send_content(user_text("reminder"), partial=True)
    assert socket.response.create.await_count == 1
    await connection.send_content(user_text("Continue"))
    assert socket.response.create.await_count == 2


@pytest.mark.asyncio
async def test_only_completed_function_arguments_are_emitted() -> None:
    socket = make_socket(
        [
            {"type": "response.function_call_arguments.delta", "delta": '{"m'},
            response_event(
                "r1",
                [
                    {
                        "type": "function_call",
                        "call_id": "a",
                        "name": "tool",
                        "arguments": '{"message":"ok"}',
                    }
                ],
            ),
        ]
    )
    connection = OpenAIRealtimeConnection(socket, "test-model", OpenAIRealtimeConfig())
    responses = [response async for response in connection.receive()]
    assert len(responses) == 2
    assert responses[0].get_function_calls()[0].args == {"message": "ok"}
    assert not responses[0].partial


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event",
    [
        {
            "type": "error",
            "error": {"code": "invalid_request", "message": "invalid configuration"},
        },
        {"type": "response.output_audio.delta", "delta": "AAA="},
        {
            "type": "response.done",
            "response": {"id": "r", "status": "incomplete", "output": []},
        },
    ],
)
async def test_protocol_failures_surface_to_session(
    event: dict[str, JsonValue],
) -> None:
    connection = OpenAIRealtimeConnection(
        make_socket([event]), "test-model", OpenAIRealtimeConfig()
    )
    with pytest.raises(LiveAgentProviderError):
        await anext(connection.receive())


@pytest.mark.asyncio
async def test_images_are_inline_context_and_close_is_idempotent() -> None:
    socket = make_socket()
    connection = OpenAIRealtimeConnection(socket, "test-model", OpenAIRealtimeConfig())
    await connection._send_content(
        types.Content(
            parts=[
                types.Part(
                    inline_data=types.Blob(
                        mime_type="image/png",
                        data=b"png",
                    )
                )
            ]
        ),
        partial=True,
    )
    item = socket.conversation.item.create.call_args.kwargs["item"]
    assert item["content"][0]["image_url"] == "data:image/png;base64,cG5n"
    socket.response.create.assert_not_awaited()
    await connection.close()
    await connection.close()
    socket.close.assert_awaited_once()
