"""High-quality GPT Image 2.5 Flare generation for theater package assets."""

from __future__ import annotations

import base64
from io import BytesIO
import os
from typing import Literal

from openai import OpenAI
from pydantic import JsonValue, TypeAdapter

from providers.image_provider import (
    ImageGenerationRequest, ImageGenerationResult, ImageProvider, ImageProviderError,
)


class OpenAIFlareImageProvider(ImageProvider):
    id = "openai-gpt-image-flare"
    display_name = "GPT Image 2.5 Flare"

    def __init__(
        self, quality: Literal["low", "medium", "high", "auto"] = "high",
        client: OpenAI | None = None,
    ) -> None:
        self.model = "gpt-image-2.5-flare"
        self.quality = quality
        if client is None:
            api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_API_KEY")
            if not api_key:
                raise ImageProviderError("OPENAI_API_KEY is not configured.")
            # Asset creation can take minutes. Avoid automatic retries of paid requests.
            client = OpenAI(api_key=api_key, timeout=300.0, max_retries=0)
        self.client = client

    def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        size = self._size(request)
        try:
            if request.references:
                images = [(ref.name, BytesIO(ref.data), ref.mime_type) for ref in request.references]
                response = self.client.images.edit(
                    model=self.model, prompt=request.prompt, image=images,
                    size=size, quality=self.quality, n=1, output_format="png",
                    background=request.background,
                )
            else:
                response = self.client.images.generate(
                    model=self.model, prompt=request.prompt, size=size,
                    quality=self.quality, n=1, output_format="png",
                    background=request.background,
                )
            data = response.data or []
            if not data or not data[0].b64_json:
                raise ImageProviderError("OpenAI returned no base64 image data.")
            content = base64.b64decode(data[0].b64_json, validate=True)
            if not content:
                raise ImageProviderError("OpenAI returned an empty image.")
            usage = TypeAdapter(dict[str, JsonValue]).validate_python(
                response.usage.model_dump(exclude_none=True) if response.usage is not None else {}
            )
        except ImageProviderError:
            raise
        except Exception as error:
            raise ImageProviderError(f"OpenAI Flare image request failed: {error}") from error
        return ImageGenerationResult(
            image_bytes=content, mime_type="image/png", provider=self.id,
            model=self.model, request_id=response._request_id, usage=usage,
        )

    @staticmethod
    def _size(request: ImageGenerationRequest) -> str:
        if (request.width is None) != (request.height is None):
            raise ImageProviderError("Both Flare image dimensions must be supplied together.")
        if request.width is not None and request.height is not None:
            width, height = request.width, request.height
            if (
                width <= 0 or height <= 0 or width % 16 or height % 16
                or max(width, height) > 3840
                or not 1 / 3 <= width / height <= 3
                or not 655360 <= width * height <= 8294400
            ):
                raise ImageProviderError("Unsupported Flare image dimensions.")
            return f"{width}x{height}"
        return {
            "1:1": "1024x1024", "16:9": "1536x864", "9:16": "864x1536",
            "3:2": "1536x1024", "2:3": "1024x1536", "4:3": "1536x1152",
            "3:4": "1152x1536", "21:9": "1792x768",
        }.get(request.resolved_aspect_ratio, "1536x864")
