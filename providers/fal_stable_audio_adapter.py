"""FAL Stable Audio 3 music audio-to-audio adapter."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from providers.music_provider import (
    MusicAdaptationRequest,
    MusicAdapter,
    MusicGenerationResult,
    MusicProviderError,
)


class FalStableAudioAdapter(MusicAdapter):
    """Call FAL's synchronous Stable Audio 3 Small Music Base A2A endpoint."""

    id = "fal-stable-audio-3-base-a2a"
    display_name = "Stable Audio 3 Small Music Base A2A (FAL)"
    model = "fal-ai/stable-audio-3/small/music/base/audio-to-audio"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        init_noise_level: float = 0.35,
        num_inference_steps: int = 50,
        guidance_scale: float = 7.0,
        output_format: str = "mp3",
        bitrate: str = "192k",
        urlopen_fn: Any = urlopen,
    ) -> None:
        self.api_key = api_key or os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")
        if not self.api_key:
            raise MusicProviderError("FAL_KEY or FAL_API_KEY is not configured for Stable Audio 3.")
        if not 0 <= init_noise_level <= 1:
            raise ValueError("init_noise_level must be between 0 and 1.")
        self.init_noise_level = init_noise_level
        self.num_inference_steps = num_inference_steps
        self.guidance_scale = guidance_scale
        self.output_format = output_format
        self.bitrate = bitrate
        self.urlopen = urlopen_fn

    def adapt(self, request: MusicAdaptationRequest) -> MusicGenerationResult:
        source_data_uri = "data:%s;base64,%s" % (
            request.source_mime_type,
            base64.b64encode(request.source_audio).decode("ascii"),
        )
        payload: dict[str, Any] = {
            "audio_url": source_data_uri,
            "prompt": request.prompt,
            "duration": request.duration_seconds,
            "init_noise_level": self.init_noise_level,
            "num_inference_steps": self.num_inference_steps,
            "guidance_scale": self.guidance_scale,
            "output_format": self.output_format,
            "bitrate": self.bitrate,
        }
        payload.update(request.options)
        try:
            response = self.urlopen(
                Request(
                    f"https://fal.run/{self.model}",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Authorization": f"Key {self.api_key}", "Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=300,
            )
            with response:
                data = json.loads(response.read().decode("utf-8"))
            audio = data.get("audio") or {}
            audio_url = audio.get("url") if isinstance(audio, Mapping) else None
            if not audio_url:
                raise MusicProviderError("Stable Audio 3 returned no audio URL.")
            download = self.urlopen(audio_url, timeout=300)
            with download:
                audio_bytes = download.read()
                reported_mime_type = audio.get("content_type") or download.headers.get_content_type()
                mime_type = reported_mime_type if reported_mime_type and reported_mime_type.startswith("audio/") else self._output_mime_type()
        except MusicProviderError:
            raise
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise MusicProviderError(f"FAL Stable Audio request failed ({exc.code}): {detail}") from exc
        except (URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise MusicProviderError(f"FAL Stable Audio request failed: {exc}") from exc

        return MusicGenerationResult(
            audio_bytes=audio_bytes,
            mime_type=mime_type,
            provider=self.id,
            model=self.model,
            request_id=data.get("request_id") or data.get("requestId"),
            usage={"seed": data.get("seed"), "prompt": data.get("prompt"), "file_size": audio.get("file_size")},
        )

    def _output_mime_type(self) -> str:
        return {
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "flac": "audio/flac",
            "ogg": "audio/ogg",
            "opus": "audio/ogg",
            "m4a": "audio/mp4",
            "aac": "audio/aac",
        }.get(self.output_format, "audio/mpeg")
