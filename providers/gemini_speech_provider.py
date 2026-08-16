"""Gemini Flash text-to-speech provider."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import re
from typing import Any, Iterable, Mapping
import wave

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
)
GEMINI_FEMALE_VOICES = ("Kore", "Aoede", "Leda")
GEMINI_MALE_VOICES = ("Puck", "Charon", "Fenrir", "Orus", "Zephyr")


class GeminiSpeechProvider(SpeechProvider):
    id = "gemini-flash-tts"
    display_name = "Gemini 3.1 Flash TTS"

    def __init__(self, model: str = "gemini-3.1-flash-tts-preview", client: Any = None) -> None:
        self.model = model
        self.client = client

    def select_voice(
        self,
        voice_tags: Iterable[str] | str | Mapping[str, Any] | None = None,
        *,
        exclude: Iterable[str] | None = None,
        **kwargs: Any,
    ) -> str:
        tags = extract_voice_tags(voice_tags)
        excluded = set(exclude or ())

        if "female" in tags and "male" not in tags:
            pool = GEMINI_FEMALE_VOICES
        elif "male" in tags and "female" not in tags:
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
                self.client = genai.Client(api_key=api_key)
            except Exception as exc:
                raise SpeechProviderError(f"Failed to initialize Gemini TTS client: {exc}") from exc

        # The Interactions API is Gemini's current TTS API.  It returns raw
        # 24 kHz PCM, which we package as WAV so browsers can preview it.
        voice = request.voice or "Kore"
        generation_config: dict[str, Any] = {"speech_config": [{"voice": voice}]}
        # Gemini's Interactions API only accepts the selected voice in
        # speech_config.  Performance direction belongs in the text prompt
        # (for example, Google's own examples use "Say cheerfully: …").
        input_text = request.text
        if request.voice_instruction:
            input_text = f"Say this with the following delivery: {request.voice_instruction}\n\n{request.text}"
        try:
            interactions = getattr(self.client, "interactions", None)
            if interactions is None:
                raise SpeechProviderError("Installed google-genai client does not support the Gemini Interactions TTS API.")
            response = interactions.create(
                model=self.model,
                input=input_text,
                response_format={"type": "audio"},
                generation_config=generation_config,
            )
            encoded_audio = self._value(response, "output_audio", "data")
            if not encoded_audio:
                raise SpeechProviderError("Gemini TTS returned no audio data.")
            pcm = base64.b64decode(encoded_audio) if isinstance(encoded_audio, str) else bytes(encoded_audio)
        except SpeechProviderError:
            raise
        except Exception as exc:
            raise SpeechProviderError(f"Gemini TTS request failed: {exc}") from exc

        sample_rate = request.sample_rate_hz or 24_000
        return SpeechSynthesisResult(
            audio_bytes=self._pcm_wav(pcm, sample_rate),
            mime_type="audio/wav",
            provider=self.id,
            model=self.model,
            request_id=self._value(response, "request_id") or self._value(response, "response_id"),
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
    def _pcm_wav(pcm: bytes, sample_rate: int) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm)
        return output.getvalue()

    @staticmethod
    def _usage(response: Any) -> dict[str, Any]:
        usage = (response.get("usage") or response.get("usage_metadata")) if isinstance(response, dict) else (getattr(response, "usage", None) or getattr(response, "usage_metadata", None))
        if hasattr(usage, "model_dump"):
            return usage.model_dump(exclude_none=True)
        return dict(usage) if isinstance(usage, dict) else {}
