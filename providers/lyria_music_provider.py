"""Google Lyria implementation of the shared music-provider contract."""

from __future__ import annotations

import os
from typing import Any

from google import genai

from providers.music_provider import (
    MusicGenerationRequest,
    MusicGenerationResult,
    MusicProvider,
    MusicProviderError,
)


class LyriaMusicProvider(MusicProvider):
    id = "lyria"
    display_name = "Google Lyria 3 Pro Preview"

    def __init__(
        self,
        model: str = "lyria-3-pro-preview",
        client: Any = None,
    ):
        self.model = model
        if client is None:
            api_key = (
                os.getenv("LYRIA_API_KEY")
                or os.getenv("GEMINI_API_KEY")
                or os.getenv("GOOGLE_API_KEY")
            )
            if not api_key:
                raise MusicProviderError("GEMINI_API_KEY, GOOGLE_API_KEY, or LYRIA_API_KEY is not configured for Lyria 3.")
            client = genai.Client(api_key=api_key)
        self.client = client

    def generate(self, request: MusicGenerationRequest) -> MusicGenerationResult:
        prompt_parts: list[str] = [request.prompt]
        if request.genre:
            prompt_parts.append(f"Genre: {request.genre}")
        if request.tempo:
            prompt_parts.append(f"Tempo: {request.tempo}")
        if request.duration_seconds:
            prompt_parts.append(f"Target duration: {request.duration_seconds} seconds")

        full_prompt = ". ".join(prompt_parts)

        try:
            # Google GenAI music / audio generation API endpoint call
            if hasattr(self.client, "generate_audio"):
                response = self.client.generate_audio(model=self.model, prompt=full_prompt)
            elif hasattr(self.client, "models") and hasattr(self.client.models, "generate_audio"):
                response = self.client.models.generate_audio(model=self.model, prompt=full_prompt)
            elif hasattr(self.client, "models") and hasattr(self.client.models, "generate_content"):
                response = self.client.models.generate_content(model=self.model, contents=[full_prompt])
            elif callable(self.client):
                response = self.client(model=self.model, prompt=full_prompt)
            else:
                raise MusicProviderError(f"Client instance {type(self.client)} does not support music generation.")
        except MusicProviderError:
            raise
        except Exception as exc:
            raise MusicProviderError(f"Lyria request failed: {exc}") from exc

        audio_bytes, mime_type = self._extract_audio(response)
        if not audio_bytes:
            raise MusicProviderError(self._failure_message(response))

        usage = self._extract_usage(response)
        request_id = getattr(response, "request_id", None) or getattr(response, "response_id", None)

        return MusicGenerationResult(
            audio_bytes=audio_bytes,
            mime_type=mime_type or "audio/mp3",
            provider=self.id,
            model=self.model,
            request_id=request_id,
            usage=usage,
        )

    @staticmethod
    def _extract_audio(response: Any) -> tuple[bytes | None, str | None]:
        if isinstance(response, bytes):
            return response, "audio/mp3"
        if isinstance(response, dict):
            audio_bytes = response.get("audio_bytes") or response.get("audio") or response.get("data")
            mime_type = response.get("mime_type", "audio/mp3")
            return audio_bytes, mime_type
        audio_bytes = getattr(response, "audio_bytes", None) or getattr(response, "audio", None)
        mime_type = getattr(response, "mime_type", None) or "audio/mp3"
        if audio_bytes:
            return audio_bytes, mime_type

        # Check candidates for inline data or audio parts
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            candidate = candidates[0]
            content = candidate.get("content") if isinstance(candidate, dict) else getattr(candidate, "content", None)
            parts = content.get("parts") if isinstance(content, dict) else getattr(content, "parts", None)
            for part in parts or []:
                inline_data = part.get("inline_data") if isinstance(part, dict) else getattr(part, "inline_data", None)
                if inline_data:
                    data = inline_data.get("data") if isinstance(inline_data, dict) else getattr(inline_data, "data", None)
                    mtype = inline_data.get("mime_type") if isinstance(inline_data, dict) else getattr(inline_data, "mime_type", None)
                    if data:
                        return data, mtype or "audio/mp3"

        return None, None

    @staticmethod
    def _failure_message(response: Any) -> str:
        if hasattr(response, "error_message") and response.error_message:
            return f"Lyria returned no audio: {response.error_message}"
        return "Lyria 3 returned no binary audio data."

    @staticmethod
    def _extract_usage(response: Any) -> dict[str, Any]:
        metadata = getattr(response, "usage_metadata", None)
        if metadata is None:
            return {}
        if hasattr(metadata, "model_dump"):
            return metadata.model_dump(exclude_none=True)
        if isinstance(metadata, dict):
            return dict(metadata)
        return {key: value for key, value in vars(metadata).items() if not key.startswith("_")}
