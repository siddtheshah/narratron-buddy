"""Provider-neutral text-to-speech contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping


class SpeechProviderError(RuntimeError):
    """A text-to-speech failure safe to expose in Test Lab diagnostics."""


@dataclass(frozen=True)
class SpeechSynthesisRequest:
    """One spoken line, with optional performance controls."""

    text: str
    voice: str | None = None
    voice_instruction: str | None = None
    speed: float | None = None
    sample_rate_hz: int | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Speech text cannot be empty.")
        if self.speed is not None and self.speed <= 0:
            raise ValueError("Speech speed must be positive.")
        if self.sample_rate_hz is not None and self.sample_rate_hz <= 0:
            raise ValueError("Speech sample rate must be positive.")


@dataclass(frozen=True)
class SpeechSynthesisResult:
    audio_bytes: bytes
    mime_type: str
    provider: str
    model: str
    request_id: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)


class SpeechProvider(ABC):
    """Synthesizes one text prompt into a browser-playable audio asset."""

    id: str
    display_name: str
    model: str

    @abstractmethod
    def synthesize(self, request: SpeechSynthesisRequest) -> SpeechSynthesisResult:
        """Generate speech or raise :class:`SpeechProviderError`."""
