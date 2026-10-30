"""FAL adapter for MiniMax H3 Max Turbo text-to-video generation."""

from __future__ import annotations

import json
import os
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, JsonValue

from providers.video_provider import (
    VideoGenerationRequest,
    VideoGenerationResult,
    VideoProvider,
    VideoProviderError,
)


class _FalVideoPayload(BaseModel):
    url: str


class _FalVideoResponse(BaseModel):
    video: _FalVideoPayload | None = None
    request_id: str | None = None
    requestId: str | None = None
    seed: JsonValue | None = None
    timings: JsonValue | None = None


class FalMinimaxVideoProvider(VideoProvider):
    """Call ``minimax/h3-max-turbo/text-to-video`` and download its generated video clip."""

    id = "fal-minimax-h3-turbo"
    display_name = "MiniMax H3 Max Turbo Video (FAL)"
    model = "minimax/h3-max-turbo/text-to-video"

    def __init__(
        self,
        api_key: str | None = None,
        request_json: Callable[[str, dict[str, JsonValue]], dict[str, JsonValue]] | None = None,
        download: Callable[[str], tuple[bytes, str]] | None = None,
        prompt_expansion_mode: str = "balanced",
        resolution: str = "768P",
        model: str | None = None,
        timeout_seconds: int = 300,
    ) -> None:
        self.api_key = api_key or os.getenv("FAL_API_KEY")
        if not self.api_key:
            raise VideoProviderError("FAL_API_KEY is not configured for MiniMax Video.")
        self._request_json = request_json or self._post_json
        self._download = download or self._download_video
        self.prompt_expansion_mode = prompt_expansion_mode
        self.resolution = resolution
        if model is not None:
            self.model = model
        self.timeout_seconds = timeout_seconds

    def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        if not request.prompt or not request.prompt.strip():
            raise VideoProviderError("MiniMax text-to-video requires a non-empty prompt.")

        payload: dict[str, JsonValue] = {
            "prompt": request.prompt.strip(),
            "resolution": self.resolution,
        }
        mode = request.prompt_expansion_mode or self.prompt_expansion_mode
        if mode:
            payload["prompt_expansion_mode"] = mode
        if request.video_duration_seconds is not None:
            payload["duration"] = request.video_duration_seconds

        response = self._request_json(self.model, payload)
        parsed = _FalVideoResponse.model_validate(response)
        if parsed.video is None or not parsed.video.url:
            raise VideoProviderError("FAL returned no generated video URL.")

        video_url = parsed.video.url
        video_bytes, mime_type = self._download(video_url)
        usage: dict[str, JsonValue] = {}
        if parsed.seed is not None:
            usage["seed"] = parsed.seed
        if parsed.timings is not None:
            usage["timings"] = parsed.timings

        return VideoGenerationResult(
            video_bytes=video_bytes,
            mime_type=mime_type or "video/mp4",
            provider=self.id,
            model=self.model,
            request_id=parsed.request_id or parsed.requestId,
            usage=usage,
            video_url=video_url,
        )

    def _post_json(self, endpoint: str, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        request = Request(
            f"https://fal.run/{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Key {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                result: dict[str, JsonValue] = json.loads(response.read().decode("utf-8"))
                return result
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise VideoProviderError(f"FAL video request failed ({exc.code}): {detail}") from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise VideoProviderError(f"FAL video request failed: {exc}") from exc

    @staticmethod
    def _download_video(url: str) -> tuple[bytes, str]:
        try:
            with urlopen(url, timeout=180) as response:
                mime_type = response.headers.get_content_type() or "video/mp4"
                return response.read(), mime_type
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise VideoProviderError(f"Unable to download FAL generated video: {exc}") from exc
