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
        elif clean.startswith("gender:"):
            clean = clean.removeprefix("gender:")
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
    voice_tags: tuple[str, ...] = ()
    accent_augmentation: bool = True
    gender_augmentation: bool = True

    def style_instruction(
        self,
        *,
        accent_augmentation: bool | None = None,
        gender_augmentation: bool | None = None,
    ) -> str | None:
        """Combine line delivery with explicit gender and accent tags without changing the transcript."""
        accent_enabled = self.accent_augmentation if accent_augmentation is None else accent_augmentation
        gender_enabled = self.gender_augmentation if gender_augmentation is None else gender_augmentation

        accents: list[str] = []
        seen_accents: set[str] = set()
        genders: list[str] = []
        seen_genders: set[str] = set()

        for tag in self.voice_tags:
            key, separator, value = tag.partition("=")
            if not separator and ":" in tag:
                key, separator, value = tag.partition(":")
            val = value.strip()
            k = key.strip().lower()
            if separator:
                if k == "accent" and val and val.casefold() not in seen_accents:
                    accents.append(val)
                    seen_accents.add(val.casefold())
                elif k == "gender" and val:
                    clean_g = val.lower().replace("-", "")
                    if clean_g in ("nb", "nonbinary"):
                        clean_g = "nonbinary"
                    if clean_g in ("male", "female", "nonbinary") and clean_g not in seen_genders:
                        genders.append(clean_g)
                        seen_genders.add(clean_g)
            else:
                clean_t = tag.strip().lower().replace("-", "")
                if clean_t in ("nb", "nonbinary"):
                    clean_t = "nonbinary"
                if clean_t in ("male", "female", "nonbinary") and clean_t not in seen_genders:
                    genders.append(clean_t)
                    seen_genders.add(clean_t)

        directions: list[str] = []
        has_gender = bool(gender_enabled and genders)
        has_accent = bool(accent_enabled and accents)

        if has_gender and has_accent:
            directions.append(
                f"Speak with a {genders[0]} voice and a clearly pronounced {', '.join(accents)} accent throughout."
            )
        elif has_gender:
            directions.append(f"Speak with a {genders[0]} voice throughout.")
        elif has_accent:
            directions.append(f"Speak with a clearly pronounced {', '.join(accents)} accent throughout.")

        if not directions:
            return self.voice_instruction

        combined_direction = " ".join(directions)
        return " ".join(part for part in ((self.voice_instruction or "").strip(), combined_direction) if part)

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("Speech text cannot be empty.")
        if self.speed is not None and self.speed <= 0:
            raise ValueError("Speech speed must be positive.")
        if self.sample_rate_hz is not None and self.sample_rate_hz <= 0:
            raise ValueError("Speech sample rate must be positive.")
        if self.voice_tags and type(self.voice_tags) is not tuple:
            object.__setattr__(self, "voice_tags", tuple(self.voice_tags))
        if type(self.accent_augmentation) is not bool:
            object.__setattr__(self, "accent_augmentation", bool(self.accent_augmentation))
        if type(self.gender_augmentation) is not bool:
            object.__setattr__(self, "gender_augmentation", bool(self.gender_augmentation))


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
