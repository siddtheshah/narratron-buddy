"""Small, provider-neutral contract for image generation and benchmarking."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from io import BytesIO
from typing import Literal, Mapping, Sequence

from PIL import Image, UnidentifiedImageError
from pydantic import JsonValue


GENERATED_WEBP_QUALITY = 95


class ImageProviderError(RuntimeError):
    """A provider-normalized generation failure safe to show in diagnostics."""


def generated_image_to_webp(content: bytes) -> bytes:
    """Encode generated rasters once, retaining dimensions, alpha, and metadata."""
    try:
        with Image.open(BytesIO(content)) as image:
            image.load()
            if image.format == "WEBP":
                return content
            exif = image.getexif()
            # Carry PNG text used by reference discovery into WebP EXIF.
            for key, tag in (("Prompt", 0x010E), ("Title", 0x9C9B), ("MetadataDescription", 0x9286)):
                text = image.info.get(key)
                if text and tag not in exif:
                    exif[tag] = str(text).encode("utf-16le") if tag == 0x9C9B else str(text)
            raster = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
            buffer = BytesIO()
            raster.save(
                buffer, "WEBP", quality=GENERATED_WEBP_QUALITY, method=6,
                exif=exif, icc_profile=image.info.get("icc_profile", b""),
                xmp=image.info.get("xmp", b""),
            )
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as error:
        raise ImageProviderError("Provider returned an image that could not be encoded as WebP.") from error


@dataclass(frozen=True)
class ImageReference:
    name: str
    data: bytes
    mime_type: str
    label: str | None = None


@dataclass(frozen=True)
class ImageGenerationRequest:
    prompt: str
    references: Sequence[ImageReference] = ()
    width: int | None = None
    height: int | None = None
    count: int = 1
    aspect_ratio: str = "16:9"
    background: Literal["auto", "opaque", "transparent"] = "auto"

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
    usage: Mapping[str, JsonValue] = field(default_factory=dict)


class ImageProvider(ABC):
    """An adapter that turns one request into exactly one generated image."""

    id: str
    display_name: str
    model: str

    def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """Generate one high-quality WebP or raise :class:`ImageProviderError`."""
        result = self._generate(request)
        return replace(result, image_bytes=generated_image_to_webp(result.image_bytes), mime_type="image/webp")

    @abstractmethod
    def _generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """Retrieve the provider's original raster before shared WebP encoding."""
