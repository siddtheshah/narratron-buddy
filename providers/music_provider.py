"""Small, provider-neutral contract for music generation and benchmarking."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping


class MusicProviderError(RuntimeError):
    """A provider-normalized music generation failure safe to show in diagnostics."""


@dataclass(frozen=True)
class MusicGenerationRequest:
    prompt: str
    duration_seconds: float = 30.0
    tempo: str | None = None
    genre: str | None = None
    count: int = 1

    def __post_init__(self) -> None:
        if self.count != 1:
            raise ValueError("Narratron music requests must request exactly one output track.")
        if self.duration_seconds <= 0:
            raise ValueError("Duration must be a positive number of seconds.")


@dataclass(frozen=True)
class MusicGenerationResult:
    audio_bytes: bytes
    mime_type: str
    provider: str
    model: str
    request_id: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)


class MusicProvider(ABC):
    """An adapter that turns one request into exactly one generated music track."""

    id: str
    display_name: str
    model: str

    @abstractmethod
    def generate(self, request: MusicGenerationRequest) -> MusicGenerationResult:
        """Generate a single music track or raise :class:`MusicProviderError`."""
