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


class LiveAgentConfig(BaseModel):
    """Operational settings shared by Live backend implementations."""

    model_config = ConfigDict(extra="ignore")

    provider: str = "gemini"
    model_id: str | None = None
    legacy_model: str | None = Field(default=None, alias="model")
    max_tool_workers: int = Field(default=3, ge=1)
    compaction: LiveAgentCompactionConfig | None = None


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

    @abstractmethod
    def create_model(self, config: LiveAgentConfig) -> BaseLlm:
        """Create a session-scoped model adapter without connecting."""

    @abstractmethod
    def build_run_config(self, config: LiveAgentConfig) -> RunConfig:
        """Translate operational settings to supported backend options."""

    @abstractmethod
    def run_live(self, request: LiveAgentRunRequest) -> AsyncGenerator[Event, None]:
        """Consume audio, activity boundaries, and content; yield ADK events."""
