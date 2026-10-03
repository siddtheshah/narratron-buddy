"""Live backend contract using ADK's shared tools, input queue, and events.

Provider adapters own vendor model connections and streaming configuration.
The session manager owns input scheduling, application callbacks, and UI events.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig
from google.adk.events import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.runners import Runner
from pydantic import BaseModel, ConfigDict, Field


class LiveAgentProviderError(RuntimeError):
    """Invalid or unavailable Live backend configuration."""


class LiveAgentCompactionConfig(BaseModel):
    """Optional context limits interpreted by the selected backend."""

    trigger_tokens: int | None = Field(default=None, gt=0)
    target_tokens: int | None = Field(default=None, gt=0)


class OpenAIRealtimeConfig(BaseModel):
    """OpenAI response and context limits; transcription is optional."""

    max_output_tokens: int = Field(default=1024, ge=1, le=4096)
    max_response_turns: int = Field(default=6, ge=1)
    post_instructions_token_limit: int = Field(default=8000, gt=0)
    retention_ratio: float = Field(default=0.8, gt=0, le=1)
    input_transcription_model: str | None = None


class LiveAgentConfig(BaseModel):
    """Operational settings shared by Live backend implementations."""

    model_config = ConfigDict(extra="ignore")

    provider: str = "gemini"
    model_id: str | None = None
    legacy_model: str | None = Field(default=None, alias="model")
    max_tool_workers: int = Field(default=3, ge=1)
    compaction: LiveAgentCompactionConfig | None = None
    openai: OpenAIRealtimeConfig = Field(default_factory=OpenAIRealtimeConfig)


@dataclass(frozen=True)
class LiveAgentRunRequest:
    """Session resources provided to a backend for one streaming invocation."""

    runner: Runner
    user_id: str
    session_id: str
    input_queue: LiveRequestQueue
    run_config: RunConfig


class LiveAgentProvider(ABC):
    """Backend adapter for ADK-compatible live model connections.

    New vendors implement an ADK BaseLlm connection adapter so existing tools,
    session state, and Event consumers do not need vendor-specific branches.
    Streams must release connections when closed or cancelled.
    """

    id: str
    display_name: str
    background_content_is_partial: bool = False
    requires_tool_reminders: bool = True
    tool_results_bypass_input_window: bool = False

    @abstractmethod
    def create_model(self, config: LiveAgentConfig) -> BaseLlm:
        """Create a session-scoped model adapter without connecting."""

    @abstractmethod
    def build_run_config(self, config: LiveAgentConfig) -> RunConfig:
        """Translate operational settings to supported backend options."""

    @abstractmethod
    def run_live(self, request: LiveAgentRunRequest) -> AsyncGenerator[Event, None]:
        """Consume audio, activity boundaries, and content; yield ADK events."""
