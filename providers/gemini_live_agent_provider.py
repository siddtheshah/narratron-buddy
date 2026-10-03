"""Gemini Developer API implementation of the Live backend contract."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import cached_property

from google import genai
from google.adk.agents.run_config import RunConfig, StreamingMode, ToolThreadPoolConfig
from google.adk.events import Event
from google.adk.models.google_llm import Gemini
from google.adk.sessions.base_session_service import GetSessionConfig
from google.genai import types

from providers.live_agent_provider import LiveAgentConfig, LiveAgentProvider, LiveAgentRunRequest


class DeveloperLiveGemini(Gemini):
    """Keep Live connections on the Developer API despite global SDK settings."""

    @cached_property
    def api_client(self) -> genai.Client:
        base_url, api_version = self._base_url_and_api_version
        return genai.Client(
            enterprise=False,
            http_options=types.HttpOptions(
                headers=self._tracking_headers(),
                retry_options=self.retry_options,
                base_url=base_url,
                api_version=api_version or None,
            ),
        )

    @cached_property
    def _live_api_client(self) -> genai.Client:
        base_url, _ = self._base_url_and_api_version
        return genai.Client(
            enterprise=False,
            http_options=types.HttpOptions(
                headers=self._tracking_headers(),
                api_version=self._live_api_version,
                base_url=base_url,
            ),
        )


class GeminiLiveAgentProvider(LiveAgentProvider):
    id = "gemini"
    display_name = "Gemini Live"

    def create_model(self, config: LiveAgentConfig) -> DeveloperLiveGemini:
        return DeveloperLiveGemini(
            model=config.model_id or config.legacy_model or "gemini-3.8-live"
        )

    def build_run_config(self, config: LiveAgentConfig) -> RunConfig:
        compaction_config = None
        compaction = config.compaction
        if compaction is not None:
            compaction_config = types.ContextWindowCompressionConfig(
                trigger_tokens=compaction.trigger_tokens,
                sliding_window=(
                    types.SlidingWindow(target_tokens=compaction.target_tokens)
                    if compaction.target_tokens is not None else None
                ),
            )
        # Gemini 3.8 Live rejects TEXT output; tool calls are separate events.
        return RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=[types.Modality.AUDIO],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=None,
            context_window_compression=compaction_config,
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(disabled=True),
                activity_handling=types.ActivityHandling.NO_INTERRUPTION,
            ),
            tool_thread_pool_config=ToolThreadPoolConfig(max_workers=config.max_tool_workers),
            get_session_config=GetSessionConfig(num_recent_events=0),
            session_resumption=types.SessionResumptionConfig(),
        )

    def run_live(self, request: LiveAgentRunRequest) -> AsyncGenerator[Event, None]:
        return request.runner.run_live(
            user_id=request.user_id,
            session_id=request.session_id,
            live_request_queue=request.input_queue,
            run_config=request.run_config,
        )
