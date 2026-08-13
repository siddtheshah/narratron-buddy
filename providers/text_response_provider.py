"""Small, provider-neutral contract for text generation and benchmarking."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


class TextResponseProviderError(RuntimeError):
    """A provider-normalized text response failure safe to show in diagnostics."""


@dataclass(frozen=True)
class TextResponseRequest:
    prompt: str
    system_instruction: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    stop_sequences: Sequence[str] = ()

    def __post_init__(self) -> None:
        if not self.prompt or not self.prompt.strip():
            raise ValueError("Text response prompt cannot be empty.")
        if self.temperature is not None and (self.temperature < 0.0 or self.temperature > 2.0):
            raise ValueError("Temperature must be between 0.0 and 2.0.")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive.")


@dataclass(frozen=True)
class TextResponseResult:
    text: str
    provider: str
    model: str
    request_id: str | None = None
    finish_reason: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)


class TextResponseProvider(ABC):
    """An adapter that turns one request into a generated text response."""

    id: str
    display_name: str
    model: str

    @abstractmethod
    def generate(self, request: TextResponseRequest) -> TextResponseResult:
        """Generate text or raise :class:`TextResponseProviderError`."""
