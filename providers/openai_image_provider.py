"""OpenAI GPT Image implementation of the shared image-provider contract."""

from __future__ import annotations

import base64
import os
from io import BytesIO
from typing import Any

from openai import OpenAI

from providers.image_provider import ImageGenerationRequest, ImageGenerationResult, ImageProvider, ImageProviderError


class OpenAIImageProvider(ImageProvider):
    id = "openai-gpt-image"
    display_name = "GPT Image 1 Mini"

    def __init__(self, model: str = "gpt-image-1-mini", quality: str = "medium", client: Any = None):
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
            "size": self._size(request.width, request.height),
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

        data = getattr(response, "data", None) or []
        if not data or not getattr(data[0], "b64_json", None):
            raise ImageProviderError("OpenAI returned no base64 image data.")
        usage = self._usage(response)
        return ImageGenerationResult(
            image_bytes=base64.b64decode(data[0].b64_json),
            mime_type="image/png",
            provider=self.id,
            model=self.model,
            request_id=getattr(response, "_request_id", None),
            usage=usage,
        )

    @staticmethod
    def _size(width: int | None, height: int | None) -> str:
        # GPT Image offers fixed landscape/square/portrait sizes. The 16:9
        # benchmark is closest to 1536x1024.
        return "1024x1024" if width and height and width == height else "1536x1024"

    @staticmethod
    def _usage(response: Any) -> dict[str, Any]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return {}
        if hasattr(usage, "model_dump"):
            return usage.model_dump(exclude_none=True)
        if isinstance(usage, dict):
            return dict(usage)
        return {key: value for key, value in vars(usage).items() if not key.startswith("_")}
