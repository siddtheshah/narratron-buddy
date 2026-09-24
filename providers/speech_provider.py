"""Provider-neutral text-to-speech contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


def extract_voice_tags(tags: Any = None) -> list[str]:
    """Extract and normalize gender tags from legacy or ``gender=value`` tags."""
    if not tags:
        return []
    if isinstance(tags, Mapping):
        tags = tags.get("voice_tags") or tags.get("gender") or []
    if isinstance(tags, str):
        tags = [tags]
    normalized = []
    for t in tags:
        clean = str(t).strip().lower().replace("-", "")
        if clean.startswith("gender="):
            clean = clean.removeprefix("gender=")
        if clean in ("nb", "nonbinary"):
            normalized.append("nonbinary")
        elif clean in ("male", "female"):
            normalized.append(clean)
    return normalized


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

    def get_supported_voice_tags(self) -> Mapping[str, tuple[str, ...]]:
        """Return supported character voice-filter fields and their values.

        Providers that expose a live voice catalog should override this method.
        The base contract keeps character generation useful for providers that
        only support the common gender selection hints.
        """
        return {"gender": ("female", "male", "nonbinary")}
