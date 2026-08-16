"""Gemini implementation of the shared image-provider contract."""

from __future__ import annotations

import base64
import os
from typing import Any

from google import genai
from google.genai import types

from providers.image_provider import (
    ImageGenerationRequest,
    ImageGenerationResult,
    ImageProvider,
    ImageProviderError,
)


class GeminiImageProvider(ImageProvider):
    id = "gemini"
    display_name = "Gemini 3.1 Flash-Lite Image"

    def __init__(
        self,
        model: str = "gemini-3.1-flash-lite-image",
        client: Any = None,
    ):
        self.model = model
        if client is None:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise ImageProviderError("GEMINI_API_KEY is not configured for the Gemini Developer API.")
            client = genai.Client(api_key=api_key)
        self.client = client

    def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        parts: list[Any] = [
            types.Part.from_bytes(data=ref.data, mime_type=ref.mime_type)
            for ref in request.references
        ]
        parts.append(request.prompt)
        aspect_ratio = self._normalize_aspect_ratio(request.resolved_aspect_ratio)
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
        )
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=parts,
                config=config,
            )
        except Exception as exc:
            raise ImageProviderError(f"Gemini request failed: {exc}") from exc

        image_bytes, mime_type = self._first_image(response)
        if not image_bytes:
            raise ImageProviderError(self._failure_message(response))
        usage = self._usage(response)
        return ImageGenerationResult(
            image_bytes=image_bytes,
            mime_type=mime_type or "image/png",
            provider=self.id,
            model=self.model,
            request_id=getattr(response, "response_id", None),
            usage=usage,
        )

    @staticmethod
    def _normalize_aspect_ratio(aspect_ratio: str) -> str:
        supported = {"1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"}
        if aspect_ratio in supported:
            return aspect_ratio
        return "16:9"

    @staticmethod
    def _first_image(response: Any) -> tuple[bytes | None, str | None]:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return None, None
        candidate = candidates[0]
        content = candidate.get("content") if isinstance(candidate, dict) else getattr(candidate, "content", None)
        parts = content.get("parts") if isinstance(content, dict) else getattr(content, "parts", None)
        for part in parts or []:
            inline_data = part.get("inline_data") if isinstance(part, dict) else getattr(part, "inline_data", None)
            if not inline_data:
                continue
            data = inline_data.get("data") if isinstance(inline_data, dict) else getattr(inline_data, "data", None)
            mime_type = inline_data.get("mime_type") if isinstance(inline_data, dict) else getattr(inline_data, "mime_type", None)
            if isinstance(data, str):
                data = base64.b64decode(data)
            if data:
                return data, mime_type
        return None, None

    @staticmethod
    def _failure_message(response: Any) -> str:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            candidate = candidates[0]
            reason = candidate.get("finish_reason") if isinstance(candidate, dict) else getattr(candidate, "finish_reason", None)
            if reason:
                return f"Gemini returned no image (finish reason: {reason})."
        return "Gemini returned no binary image data."

    @staticmethod
    def _usage(response: Any) -> dict[str, Any]:
        metadata = getattr(response, "usage_metadata", None)
        if metadata is None:
            return {}
        if hasattr(metadata, "model_dump"):
            return metadata.model_dump(exclude_none=True)
        if isinstance(metadata, dict):
            return dict(metadata)
        return {key: value for key, value in vars(metadata).items() if not key.startswith("_")}
