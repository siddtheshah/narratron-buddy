"""FAL adapter for MiniMax H3 Turbo text-to-video generation."""

from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from providers.video_provider import (
    VideoGenerationRequest,
    VideoGenerationResult,
    VideoProvider,
    VideoProviderError,
)


class FalMinimaxVideoProvider(VideoProvider):
    """Call ``fal-ai/minimax-h3-turbo/text-to-video`` and download its generated video clip."""

    id = "fal-minimax-h3-turbo"
    display_name = "MiniMax H3 Turbo Video (FAL)"
    model = "fal-ai/minimax-h3-turbo/text-to-video"

    def __init__(
        self,
        api_key: str | None = None,
        request_json: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        download: Callable[[str], tuple[bytes, str]] | None = None,
        prompt_expansion_mode: str = "balanced",
        timeout_seconds: int = 300,
    ):
        self.api_key = api_key or os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")
        if not self.api_key:
            raise VideoProviderError("FAL_KEY or FAL_API_KEY is not configured for MiniMax Video.")
        self._request_json = request_json or self._post_json
        self._download = download or self._download_video
        self.prompt_expansion_mode = prompt_expansion_mode
        self.timeout_seconds = timeout_seconds

    def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        if not request.prompt or not request.prompt.strip():
            raise VideoProviderError("MiniMax text-to-video requires a non-empty prompt.")

        payload: dict[str, Any] = {
            "prompt": request.prompt.strip(),
        }
        mode = request.prompt_expansion_mode or self.prompt_expansion_mode
        if mode:
            payload["prompt_expansion_mode"] = mode
        if request.video_duration_seconds is not None:
            payload["duration"] = request.video_duration_seconds

        response = self._request_json(self.model, payload)
        video_field = response.get("video")
        video_url: str | None = None
        if isinstance(video_field, dict):
            video_url = video_field.get("url")
        elif isinstance(video_field, str):
            video_url = video_field

        if not video_url:
            raise VideoProviderError("FAL returned no generated video URL.")

        video_bytes, mime_type = self._download(video_url)
        return VideoGenerationResult(
            video_bytes=video_bytes,
            mime_type=mime_type or "video/mp4",
            provider=self.id,
            model=self.model,
            request_id=response.get("request_id") or response.get("requestId"),
            usage={key: response[key] for key in ("seed", "timings") if key in response},
            video_url=video_url,
        )

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"https://fal.run/{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Key {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
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
