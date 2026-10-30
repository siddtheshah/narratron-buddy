"""FAL adapter for MiniMax H3 Max Turbo text-to-video and image-to-video generation."""

from __future__ import annotations

import base64
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
    """Call FAL MiniMax H3 Max Turbo text-to-video or image-to-video."""

    id = "fal-minimax-h3-turbo"
    display_name = "MiniMax H3 Max Turbo Video (FAL)"
    model = "minimax/h3-max-turbo/text-to-video"
    text_model = "minimax/h3-max-turbo/text-to-video"
    image_model = "minimax/h3-max-turbo/image-to-video"

    def __init__(
        self,
        api_key: str | None = None,
        request_json: Callable[[str, dict[str, JsonValue]], dict[str, JsonValue]] | None = None,
        download: Callable[[str], tuple[bytes, str]] | None = None,
        prompt_expansion_mode: str = "balanced",
        resolution: str = "768P",
        model: str | None = None,
        timeout_seconds: int = 300,
        id: str | None = None,
        display_name: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("FAL_API_KEY") or os.getenv("FAL_KEY")
        if not self.api_key:
            raise VideoProviderError("FAL_API_KEY is not configured for MiniMax Video.")
        self._request_json = request_json or self._post_json
        self._download = download or self._download_video
        self.prompt_expansion_mode = prompt_expansion_mode
        self.resolution = resolution
        if model is not None:
            self.model = model
        if id is not None:
            self.id = id
        elif "image-to-video" in self.model:
            self.id = "fal-minimax-h3-image-to-video"
        if display_name is not None:
            self.display_name = display_name
        elif "image-to-video" in self.model:
            self.display_name = "MiniMax H3 Max Turbo Image-to-Video (FAL)"
        self.timeout_seconds = timeout_seconds

    @property
    def is_image_to_video(self) -> bool:
        return "image-to-video" in self.model

    @staticmethod
    def _data_uri(data: bytes, mime_type: str) -> str:
        encoded = base64.b64encode(data).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        if not request.prompt or not request.prompt.strip():
            raise VideoProviderError("MiniMax text-to-video requires a non-empty prompt.")

        image_url = request.image_url
        if not image_url and request.image_bytes:
            image_url = self._data_uri(
                request.image_bytes, request.image_mime_type or "image/png"
            )

        end_image_url = request.end_image_url
        if not end_image_url and request.end_image_bytes:
            end_image_url = self._data_uri(
                request.end_image_bytes, request.image_mime_type or "image/png"
            )

        if self.is_image_to_video and not image_url:
            raise VideoProviderError("MiniMax image-to-video requires a starting image.")

        endpoint = self.model
        if self.model == self.text_model and image_url is not None:
            endpoint = self.image_model

        payload: dict[str, JsonValue] = {
            "prompt": request.prompt.strip(),
            "resolution": self.resolution,
        }
        if image_url is not None:
            payload["image_url"] = image_url
        if end_image_url is not None:
            payload["end_image_url"] = end_image_url

        mode = request.prompt_expansion_mode or self.prompt_expansion_mode
        if mode:
            payload["prompt_expansion_mode"] = mode
        if request.video_duration_seconds is not None:
            payload["duration"] = request.video_duration_seconds

        response = self._request_json(endpoint, payload)
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
            model=endpoint,
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
