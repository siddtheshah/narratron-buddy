"""Small, provider-neutral contract for image generation and benchmarking."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


class ImageProviderError(RuntimeError):
    """A provider-normalized generation failure safe to show in diagnostics."""


@dataclass(frozen=True)
class ImageReference:
    name: str
    data: bytes
    mime_type: str


@dataclass(frozen=True)
class ImageGenerationRequest:
    prompt: str
    references: Sequence[ImageReference] = ()
    width: int | None = None
    height: int | None = None
    count: int = 1

    def __post_init__(self) -> None:
        if self.count != 1:
            raise ValueError("Narratron image requests must request exactly one output image.")


@dataclass(frozen=True)
class ImageGenerationResult:
    image_bytes: bytes
    mime_type: str
    provider: str
    model: str
    request_id: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)


class ImageProvider(ABC):
    """An adapter that turns one request into exactly one generated image."""

    id: str
    display_name: str
    model: str

    @abstractmethod
    def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """Generate a single image or raise :class:`ImageProviderError`."""
