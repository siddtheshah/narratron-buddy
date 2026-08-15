"""ByteDance Seed Speech v2 text-to-speech via FAL."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from providers.speech_provider import SpeechProvider, SpeechProviderError, SpeechSynthesisRequest, SpeechSynthesisResult


class FalSeedSpeechProvider(SpeechProvider):
    id = "fal-seed-speech"
    display_name = "ByteDance Seed Speech v2 (FAL)"
    model = "fal-ai/bytedance/seed-speech/tts/v2"

    def __init__(self, *, api_key: str | None = None, request_json: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None, download: Callable[[str], tuple[bytes, str]] | None = None, output_format: str = "mp3", sample_rate_hz: int = 24_000) -> None:
        self.api_key = api_key or os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")
        if not self.api_key:
            raise SpeechProviderError("FAL_KEY or FAL_API_KEY is not configured for Seed Speech.")
        self.output_format = output_format
        self.sample_rate_hz = sample_rate_hz
        self._request_json = request_json or self._post_json
        self._download = download or self._download_audio

    def synthesize(self, request: SpeechSynthesisRequest) -> SpeechSynthesisResult:
        payload: dict[str, Any] = {
            "text": request.text,
            "voice": request.voice or "stokie_en",
            "output_format": self.output_format,
            "sample_rate": request.sample_rate_hz or self.sample_rate_hz,
        }
        if request.speed is not None:
            payload["speed"] = request.speed
        if request.voice_instruction:
            payload["voice_instruction"] = request.voice_instruction
        response = self._request_json(self.model, payload)
        audio = response.get("audio") or {}
        url = audio.get("url") if isinstance(audio, Mapping) else None
        if not url:
            raise SpeechProviderError("Seed Speech returned no audio URL.")
        audio_bytes, mime_type = self._download(url)
        return SpeechSynthesisResult(audio_bytes=audio_bytes, mime_type=mime_type, provider=self.id, model=self.model, request_id=response.get("request_id") or response.get("requestId"), usage={key: response[key] for key in ("seed", "timings") if key in response})

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(f"https://fal.run/{endpoint}", data=json.dumps(payload).encode("utf-8"), headers={"Authorization": f"Key {self.api_key}", "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=180) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise SpeechProviderError(f"FAL Seed Speech request failed ({exc.code}): {detail}") from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise SpeechProviderError(f"FAL Seed Speech request failed: {exc}") from exc

    @staticmethod
    def _download_audio(url: str) -> tuple[bytes, str]:
        try:
            with urlopen(url, timeout=180) as response:
                return response.read(), response.headers.get_content_type() or "audio/mpeg"
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise SpeechProviderError(f"Unable to download Seed Speech audio: {exc}") from exc
