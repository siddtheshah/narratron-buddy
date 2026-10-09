"""OpenAI GPT Image implementation of the shared image-provider contract."""

from __future__ import annotations

import base64
from io import BytesIO
import os

from openai import OpenAI
from pydantic import JsonValue, TypeAdapter

from providers.image_provider import (
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageProvider,
    ImageProviderError,
)


class OpenAIImageProvider(ImageProvider):
    id = "openai-gpt-image"
    display_name = "GPT Image 2"

    def __init__(
        self,
        model: str = "gpt-image-2",
        quality: str = "medium",
        client: OpenAI | None = None,
    ) -> None:
        self.model = model
        self.quality = quality
        if client is None:
            api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_API_KEY")
            if not api_key:
                raise ImageProviderError("OPENAI_API_KEY is not configured.")
            client = OpenAI(api_key=api_key)
        self.client = client

    def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        kwargs = {
            "model": self.model,
            "prompt": request.prompt,
            "size": self._size(request.width, request.height, request.resolved_aspect_ratio),
            "quality": self.quality,
            "n": 1,
            "output_format": "png",
        }
        try:
            if request.references:
                images = [(reference.name, BytesIO(reference.data), reference.mime_type) for reference in request.references]
                response = self.client.images.edit(image=images, **kwargs)
            else:
                response = self.client.images.generate(**kwargs)
        except Exception as exc:
            raise ImageProviderError(f"OpenAI image request failed: {exc}") from exc

        data = response.data or []
        if not data or not data[0].b64_json:
            raise ImageProviderError("OpenAI returned no base64 image data.")
        content = base64.b64decode(data[0].b64_json, validate=True)
        if not content:
            raise ImageProviderError("OpenAI returned an empty image.")
        usage: dict[str, JsonValue] = (
            TypeAdapter(dict[str, JsonValue]).validate_python(response.usage.model_dump(exclude_none=True))
            if response.usage is not None
            else {}
        )
        return ImageGenerationResult(
            image_bytes=content,
            mime_type="image/png",
            provider=self.id,
            model=self.model,
            request_id=response._request_id,
            usage=usage,
        )

    @staticmethod
    def _size(width: int | None, height: int | None, aspect_ratio: str | None = None) -> str:
        # GPT Image offers fixed landscape/square/portrait sizes. The 16:9
        # benchmark is closest to 1536x1024.
        if width and height and width == height:
            return "1024x1024"
        if aspect_ratio == "1:1":
            return "1024x1024"
        if (width and height and height > width) or aspect_ratio in ("9:16", "3:4", "2:3"):
            return "1024x1536"
        return "1536x1024"
