"""Provider-neutral text-to-speech contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


def extract_voice_tags(tags: Any = None) -> list[str]:
    """Extract and normalize voice tags ('male' or 'female')."""
    if not tags:
        return []
    if isinstance(tags, Mapping):
        tags = tags.get("voice_tags", [])
    if isinstance(tags, str):
        tags = [tags]
    return [str(t).strip().lower() for t in tags if str(t).strip().lower() in ("male", "female")]


def extract_character_description(character: str | Mapping[str, Any] | None) -> str:
    """Normalize string or structured character dictionary into a text description."""
    if not character:
        return ""
    if isinstance(character, str):
        return character.strip()
    if isinstance(character, Mapping):
        parts: list[str] = []
        for key in ("name", "gender", "role", "description", "personality", "motivation", "quirk", "tone", "background"):
            val = character.get(key)
            if val:
                parts.append(str(val))
        for key, val in character.items():
            if key not in ("name", "gender", "role", "description", "personality", "motivation", "quirk", "tone", "background") and isinstance(val, (str, int, float)):
                parts.append(f"{key}: {val}")
        return " ".join(parts).strip()
    return str(character).strip()


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

    def select_voice(
        self,
        voice_tags: Iterable[str] | str | Mapping[str, Any] | None = None,
        *,
        exclude: Iterable[str] | None = None,
        **kwargs: Any,
    ) -> str:
        """Select a voice based on voice tags (e.g. 'male' or 'female')."""
        return getattr(self, "model", "") or "default"

