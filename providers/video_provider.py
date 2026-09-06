"""Small, provider-neutral contract for video generation and benchmarking."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping


class VideoProviderError(RuntimeError):
    """A provider-normalized video generation failure safe to show in diagnostics."""


@dataclass(frozen=True)
class VideoGenerationRequest:
    prompt: str
    aspect_ratio: str = "16:9"
    video_duration_seconds: int | None = None
    prompt_expansion_mode: str | None = None


@dataclass(frozen=True)
class VideoGenerationResult:
    video_bytes: bytes
    mime_type: str
    provider: str
    model: str
    request_id: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    video_url: str | None = None


class VideoProvider(ABC):
    """An adapter that turns one text prompt into a generated video clip."""

    id: str
    display_name: str
    model: str

    @abstractmethod
    def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        """Generate a single video or raise :class:`VideoProviderError`."""
