"""Gemini Flash text-to-speech provider."""

from __future__ import annotations

import base64
import os
import time
from typing import Any, Iterable, Mapping

from google import genai

from providers.speech_provider import (
    SpeechProvider,
    SpeechProviderError,
    SpeechSynthesisRequest,
    SpeechSynthesisResult,
    extract_voice_tags,
)

GEMINI_VOICES = (
    "Kore", "Puck", "Charon", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
)
GEMINI_FEMALE_VOICES = ("Kore", "Aoede", "Leda")
GEMINI_MALE_VOICES = ("Puck", "Charon", "Fenrir", "Orus", "Zephyr")


class GeminiSpeechProvider(SpeechProvider):
    id = "gemini-flash-tts"
    display_name = "Gemini 3.8 Flash TTS"

    def __init__(
        self,
        model: str = "gemini-3.8-flash-tts",
        client: Any = None,
        *,
        max_attempts: int = 3,
        retry_delay_seconds: float = 0.25,
    ) -> None:
        self.model = model
        self.client = client
        self.max_attempts = max(1, int(max_attempts))
        self.retry_delay_seconds = max(0.0, float(retry_delay_seconds))

    def select_voice(
        self,
        voice_tags: Iterable[str] | str | Mapping[str, Any] | None = None,
        *,
        exclude: Iterable[str] | None = None,
        **kwargs: Any,
    ) -> str:
        tags = extract_voice_tags(voice_tags)
        excluded = set(exclude or ())

        if "female" in tags and "male" not in tags and "nonbinary" not in tags:
            pool = GEMINI_FEMALE_VOICES
        elif "male" in tags and "female" not in tags and "nonbinary" not in tags:
            pool = GEMINI_MALE_VOICES
        else:
            pool = GEMINI_VOICES

        available = [v for v in pool if v not in excluded]
        if not available:
            available = [v for v in GEMINI_VOICES if v not in excluded]
        if not available:
            available = list(pool) or list(GEMINI_VOICES)

        return available[0]

    def synthesize(self, request: SpeechSynthesisRequest) -> SpeechSynthesisResult:
        if self.client is None:
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            if not api_key:
                raise SpeechProviderError("GEMINI_API_KEY or GOOGLE_API_KEY is not configured for Gemini TTS.")
            try:
                # TTS uses the Gemini Developer API's Interactions endpoint.
                # Be explicit so ambient Vertex/Enterprise flags used by the
                # rest of the app cannot redirect this API-key client.
                self.client = genai.Client(
                    api_key=api_key,
                    enterprise=False,
                    vertexai=False,
                )
            except Exception as exc:
                raise SpeechProviderError(f"Failed to initialize Gemini TTS client: {exc}") from exc

        # Gemini 3.8's unary Interactions API returns a complete WAV asset.
        # Unlike the older preview, delivery direction is sent as structured
        # speech metadata, keeping it out of the literal transcript.
        voice = request.voice or "Kore"
        generation_config: dict[str, Any] = {"speech_config": [{"voice": voice}]}
        annotations: list[dict[str, str]] = []
        if request.voice_instruction:
            annotations.append({"type": "speech_metadata", "style": request.voice_instruction})
        input_data = [{
            "type": "user_input",
            "content": [{
                "type": "text",
                "text": request.text,
                **({"annotations": annotations} if annotations else {}),
            }],
        }]
        response_format: dict[str, Any] = {"type": "audio", "mime_type": "audio/wav"}
        if request.sample_rate_hz:
            response_format["sample_rate"] = request.sample_rate_hz

        interactions = getattr(self.client, "interactions", None)
        if interactions is None:
            raise SpeechProviderError("Installed google-genai client does not support the Gemini Interactions TTS API.")

        response: Any = None
        pcm: bytes | None = None
        last_error: Exception | None = None
        attempts_made = 0
        for attempt in range(self.max_attempts):
            attempts_made = attempt + 1
            try:
                response = interactions.create(
                    model=self.model,
                    input=input_data,
                    response_format=response_format,
                    generation_config=generation_config,
                )
                encoded_audio = self._value(response, "output_audio", "data")
                if not encoded_audio:
                    raise ValueError("Gemini TTS returned no audio data.")
                pcm = base64.b64decode(encoded_audio) if isinstance(encoded_audio, str) else bytes(encoded_audio)
                break
            except Exception as exc:
                last_error = exc
                if not self._is_retryable(exc):
                    break
                if attempt + 1 < self.max_attempts and self.retry_delay_seconds:
                    time.sleep(self.retry_delay_seconds * (2 ** attempt))
        if pcm is None:
            detail = str(last_error) if last_error else "unknown failure"
            raise SpeechProviderError(
                f"Gemini TTS request failed after {attempts_made} attempt(s): {detail}"
            ) from last_error

        return SpeechSynthesisResult(
            audio_bytes=pcm,
            mime_type="audio/wav",
            provider=self.id,
            model=self.model,
            request_id=(
                self._value(response, "id")
                or self._value(response, "request_id")
                or self._value(response, "response_id")
            ),
            usage=self._usage(response),
        )

    @staticmethod
    def _value(obj: Any, *path: str) -> Any:
        for key in path:
            obj = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
            if obj is None:
                return None
        return obj

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Retry transient transport/rate-limit/server failures, not bad requests."""
        status = getattr(exc, "code", None)
        if not isinstance(status, int):
            status = getattr(exc, "status_code", None)
        if isinstance(status, int):
            return status == 429 or status >= 500
        return True

    @staticmethod
    def _usage(response: Any) -> dict[str, Any]:
        usage = (response.get("usage") or response.get("usage_metadata")) if isinstance(response, dict) else (getattr(response, "usage", None) or getattr(response, "usage_metadata", None))
        if hasattr(usage, "model_dump"):
            return usage.model_dump(exclude_none=True)
        return dict(usage) if isinstance(usage, dict) else {}
