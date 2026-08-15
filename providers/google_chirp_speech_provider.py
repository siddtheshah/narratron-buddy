"""Google Cloud Chirp 3: HD text-to-speech provider."""

from __future__ import annotations

import os
from typing import Any

from providers.speech_provider import SpeechProvider, SpeechProviderError, SpeechSynthesisRequest, SpeechSynthesisResult


class GoogleChirpSpeechProvider(SpeechProvider):
    id = "google-chirp-3-hd"
    display_name = "Google Cloud Chirp 3: HD"

    def __init__(self, model: str = "en-US-Chirp3-HD-Charon", client: Any = None, texttospeech_module: Any = None) -> None:
        self.model = model
        if texttospeech_module is None:
            try:
                from google.cloud import texttospeech as texttospeech_module
            except ImportError as exc:
                raise SpeechProviderError("google-cloud-texttospeech is required for Google Cloud Chirp TTS.") from exc
        self.texttospeech = texttospeech_module
        try:
            self.client = client or texttospeech_module.TextToSpeechClient()
        except Exception as exc:
            raise SpeechProviderError(f"Failed to initialize Google Cloud Text-to-Speech: {exc}") from exc

    def synthesize(self, request: SpeechSynthesisRequest) -> SpeechSynthesisResult:
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
