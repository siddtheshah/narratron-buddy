"""Small, provider-neutral contract for music generation and benchmarking."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


class MusicProviderError(RuntimeError):
    """A provider-normalized music generation failure safe to show in diagnostics."""


@dataclass(frozen=True)
class MusicGenerationRequest:
    prompt: str
    duration_seconds: float = 30.0
    tempo: str | None = None
    genre: str | None = None
    count: int = 1
    on_progress: Callable[[str, Mapping[str, Any]], None] | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.count != 1:
            raise ValueError("Narratron music requests must request exactly one output track.")
        if self.duration_seconds <= 0:
            raise ValueError("Duration must be a positive number of seconds.")


@dataclass(frozen=True)
class MusicAudioArtifact:
    """An additional audio artifact produced during a composed generation."""

    audio_bytes: bytes
    mime_type: str
    provider: str
    model: str
    request_id: str | None = None


@dataclass(frozen=True)
class MusicGenerationResult:
    audio_bytes: bytes
    mime_type: str
    provider: str
    model: str
    request_id: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    artifacts: Mapping[str, MusicAudioArtifact] = field(default_factory=dict)


@dataclass(frozen=True)
class MusicAdaptationRequest:
    """A source track plus an instruction for an audio-to-audio provider."""

    source_audio: bytes
    source_mime_type: str
    prompt: str
    duration_seconds: float = 30.0
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_audio:
            raise ValueError("An adaptation request requires source audio.")
        if self.duration_seconds <= 0:
            raise ValueError("Duration must be a positive number of seconds.")


class MusicAdapter(ABC):
    """Transforms a track created by any :class:`MusicProvider`."""

    id: str
    display_name: str
    model: str

    @abstractmethod
    def adapt(self, request: MusicAdaptationRequest) -> MusicGenerationResult:
        """Return one adapted track or raise :class:`MusicProviderError`."""


class MusicProvider(ABC):
    """An adapter that turns one request into exactly one generated music track."""

    id: str
    display_name: str
    model: str

    @abstractmethod
    def generate(self, request: MusicGenerationRequest) -> MusicGenerationResult:
        """Generate a single music track or raise :class:`MusicProviderError`."""
