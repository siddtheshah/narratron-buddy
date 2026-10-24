"""Google Lyria implementation of the shared music-provider contract."""

from __future__ import annotations

import base64
import binascii
import os
from typing import Protocol

from google import genai
from pydantic import JsonValue

from providers.music_provider import (
    MusicGenerationRequest,
    MusicGenerationResult,
    MusicProvider,
    MusicProviderError,
)


class LyriaAudio(Protocol):
    """The documented convenience audio field on a Lyria interaction."""

    data: bytes | str
    mime_type: str | None


class LyriaUsage(Protocol):
    """Usage metadata returned by the Google GenAI SDK."""

    def model_dump(self, *, exclude_none: bool = False) -> dict[str, JsonValue]: ...


class LyriaInteraction(Protocol):
    """The portion of an Interactions API result used by this provider."""

    id: str | None
    output_audio: LyriaAudio | None
    usage: LyriaUsage | None


class LyriaInteractions(Protocol):
    """Shared music-generation endpoint for every supported Lyria model."""

    def create(self, *, model: str, input: str) -> LyriaInteraction: ...


class LyriaClient(Protocol):
    interactions: LyriaInteractions


class LyriaMusicProvider(MusicProvider):
    id = "lyria"
    display_name = "Google Lyria 3 Pro Preview"

    def __init__(
        self,
        model: str = "lyria-3-pro-preview",
        client: LyriaClient | None = None,
        provider_id: str | None = None,
        display_name: str | None = None,
    ) -> None:
        self.model = model
        if provider_id is not None:
            self.id = provider_id
        elif "3.5" in model:
            self.id = "lyria-3.5"
        else:
            self.id = "lyria"

        if display_name is not None:
            self.display_name = display_name
        elif "3.5" in model:
            self.display_name = "Google Lyria 3.5"
        elif "clip" in model:
            self.display_name = "Google Lyria 3 Clip Preview"
        else:
            self.display_name = "Google Lyria 3 Pro Preview"

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
        prompt = self._build_prompt(request)
        try:
            interaction = self.client.interactions.create(model=self.model, input=prompt)
        except Exception as exc:
            raise MusicProviderError(f"Lyria request failed: {exc}") from exc

        audio = interaction.output_audio
        if audio is None:
            raise MusicProviderError("Lyria returned no binary audio data.")

        return MusicGenerationResult(
            audio_bytes=self._decode_audio(audio.data),
            mime_type=audio.mime_type or "audio/mp3",
            provider=self.id,
            model=self.model,
            request_id=interaction.id,
            usage=interaction.usage.model_dump(exclude_none=True) if interaction.usage else {},
        )

    @staticmethod
    def _build_prompt(request: MusicGenerationRequest) -> str:
        prompt_parts: list[str] = [request.prompt]
        if request.genre:
            prompt_parts.append(f"Genre: {request.genre}")
        if request.tempo:
            prompt_parts.append(f"Tempo: {request.tempo}")
        if request.duration_seconds:
            prompt_parts.append(f"Target duration: {request.duration_seconds} seconds")
        return ". ".join(prompt_parts)

    @staticmethod
    def _decode_audio(data: bytes | str) -> bytes:
        if type(data) is bytes:
            return data
        try:
            return base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise MusicProviderError("Lyria returned invalid base64 audio data.") from exc
