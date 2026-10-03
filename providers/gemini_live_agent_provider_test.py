"""Verify Gemini translation and streaming without live API requests."""

from collections.abc import AsyncGenerator
from unittest.mock import MagicMock

import pytest
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import StreamingMode
from google.adk.events import Event
from google.adk.runners import Runner
from google.genai import types

from providers import (
    GeminiLiveAgentProvider,
    LiveAgentConfig,
    LiveAgentProviderError,
    LiveAgentRunRequest,
    get_live_agent_provider,
)


def test_registry_rejects_unknown_backend() -> None:
    assert get_live_agent_provider().id == "gemini"
    with pytest.raises(LiveAgentProviderError, match="unsupported"):
        get_live_agent_provider("unsupported")


def test_gemini_preserves_manual_vad_and_compaction() -> None:
    config = LiveAgentConfig.model_validate({
        "max_tool_workers": 7,
        "compaction": {"trigger_tokens": 20000, "target_tokens": 8000},
    })
    run_config = GeminiLiveAgentProvider().build_run_config(config)
    assert run_config.streaming_mode == StreamingMode.BIDI
    assert run_config.response_modalities == [types.Modality.AUDIO]
    assert run_config.input_audio_transcription is not None
    assert run_config.output_audio_transcription is None
    assert run_config.realtime_input_config.automatic_activity_detection.disabled
    assert run_config.realtime_input_config.activity_handling == types.ActivityHandling.NO_INTERRUPTION
    assert run_config.context_window_compression.trigger_tokens == 20000
    assert run_config.context_window_compression.sliding_window.target_tokens == 8000
    assert run_config.tool_thread_pool_config.max_workers == 7
    assert run_config.get_session_config.num_recent_events == 0
    assert run_config.session_resumption is not None


def test_gemini_without_compaction_and_legacy_model_selection() -> None:
    provider = GeminiLiveAgentProvider()
    assert provider.build_run_config(LiveAgentConfig()).context_window_compression is None
    config = LiveAgentConfig.model_validate({"model": "legacy", "model_id": "preferred"})
    assert provider.create_model(config).model == "preferred"
    assert provider.create_model(LiveAgentConfig.model_validate({"model": "legacy"})).model == "legacy"


@pytest.mark.asyncio
async def test_stream_passes_session_resources_and_releases_connection() -> None:
    closed: list[bool] = []
    event = Event(author="agent")

    async def events() -> AsyncGenerator[Event, None]:
        try:
            yield event
        finally:
            closed.append(True)

    runner = MagicMock(spec=Runner)
    runner.run_live.return_value = events()
    provider = GeminiLiveAgentProvider()
    request = LiveAgentRunRequest(
        runner=runner, user_id="user", session_id="session",
        input_queue=LiveRequestQueue(), run_config=provider.build_run_config(LiveAgentConfig()),
    )
    stream = provider.run_live(request)
    assert await anext(stream) is event
    await stream.aclose()
    assert closed == [True]
    runner.run_live.assert_called_once_with(
        user_id="user", session_id="session", live_request_queue=request.input_queue,
        run_config=request.run_config,
    )
