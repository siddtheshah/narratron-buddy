"""Google Cloud Chirp 3: HD text-to-speech provider."""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Iterable, Mapping

from providers.speech_provider import (
    SpeechProvider,
    SpeechProviderError,
    SpeechSynthesisRequest,
    SpeechSynthesisResult,
    extract_voice_tags,
)

CHIRP_VOICES = (
    "en-US-Chirp3-HD-Charon", "en-US-Chirp3-HD-Puck", "en-US-Chirp3-HD-Fenrir",
    "en-US-Chirp3-HD-Aoede", "en-US-Chirp3-HD-Kore", "en-US-Chirp3-HD-Leda",
)
CHIRP_FEMALE_VOICES = (
    "en-US-Chirp3-HD-Aoede", "en-US-Chirp3-HD-Kore", "en-US-Chirp3-HD-Leda",
)
CHIRP_MALE_VOICES = (
    "en-US-Chirp3-HD-Charon", "en-US-Chirp3-HD-Puck", "en-US-Chirp3-HD-Fenrir",
)


class GoogleChirpSpeechProvider(SpeechProvider):
    id = "google-chirp-3-hd"
    display_name = "Google Cloud Chirp 3: HD"

    def __init__(self, model: str = "en-US-Chirp3-HD-Charon", client: Any = None, texttospeech_module: Any = None) -> None:
        self.model = model
        self.client = client
        self.texttospeech = texttospeech_module

    def _ensure_client(self) -> None:
        if self.texttospeech is None:
            try:
                from google.cloud import texttospeech as texttospeech_module
                self.texttospeech = texttospeech_module
            except ImportError as exc:
                raise SpeechProviderError("google-cloud-texttospeech is required for Google Cloud Chirp TTS.") from exc
        if self.client is None:
            try:
                self.client = self.texttospeech.TextToSpeechClient()
            except Exception as exc:
                raise SpeechProviderError(f"Failed to initialize Google Cloud Text-to-Speech: {exc}") from exc

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
            pool = CHIRP_FEMALE_VOICES
        elif "male" in tags and "female" not in tags:
            pool = CHIRP_MALE_VOICES
        else:
            pool = CHIRP_VOICES

        available = [v for v in pool if v not in excluded]
        if not available:
            available = [v for v in CHIRP_VOICES if v not in excluded]
        if not available:
            available = list(pool) or list(CHIRP_VOICES)

        return available[0]

    def synthesize(self, request: SpeechSynthesisRequest) -> SpeechSynthesisResult:
        self._ensure_client()
        voice_name = request.voice or self.model
        language_code = "-".join(voice_name.split("-")[:2]) if voice_name.count("-") >= 1 else "en-US"
        try:
            response = self.client.synthesize_speech(
                input=self.texttospeech.SynthesisInput(text=request.text),
                voice=self.texttospeech.VoiceSelectionParams(name=voice_name, language_code=language_code),
                audio_config=self.texttospeech.AudioConfig(
                    audio_encoding=self.texttospeech.AudioEncoding.MP3,
                    speaking_rate=request.speed or 1.0,
                ),
            )
        except Exception as exc:
            raise SpeechProviderError(f"Google Cloud Chirp TTS request failed: {exc}") from exc
        audio_content = getattr(response, "audio_content", None)
        if not audio_content:
            raise SpeechProviderError("Google Cloud Chirp TTS returned no audio data.")
        return SpeechSynthesisResult(
            audio_bytes=bytes(audio_content),
            mime_type="audio/mpeg",
            provider=self.id,
            model=voice_name,
            request_id=getattr(response, "request_id", None),
        )
