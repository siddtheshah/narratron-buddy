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
    aspect_ratio: str = "16:9"

    def __post_init__(self) -> None:
        if self.count != 1:
            raise ValueError("Narratron image requests must request exactly one output image.")

    @property
    def resolved_aspect_ratio(self) -> str:
        """Return the effective aspect ratio string (e.g. '16:9', '1:1', '4:3')."""
        if self.width and self.height:
            ratio = self.width / self.height
            if ratio > 2.0:
                return "21:9"
            if ratio >= 1.6:
                return "16:9"
            if ratio >= 1.4:
                return "3:2"
            if ratio >= 1.2:
                return "4:3"
            if ratio >= 0.85:
                return "1:1"
            if ratio >= 0.7:
                return "3:4"
            if ratio >= 0.6:
                return "2:3"
            return "9:16"
        return self.aspect_ratio or "16:9"


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
