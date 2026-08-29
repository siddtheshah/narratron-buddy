"""FAL adapter for Qwen Image Layered source-image decomposition."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from providers.image_provider import ImageProviderError


@dataclass(frozen=True)
class LayeredImageRequest:
    """A source image plus the layer plan used to ground its decomposition."""

    image_bytes: bytes
    mime_type: str
    prompt: str
    num_layers: int


@dataclass(frozen=True)
class LayeredImageResult:
    images: list[tuple[bytes, str]]
    request_id: str | None = None
    usage: dict[str, Any] | None = None


class FalQwenLayeredProvider:
    """Call ``fal-ai/qwen-image-layered`` and download its RGBA layer files."""

    id = "fal-qwen-image-layered"
    model = "fal-ai/qwen-image-layered"

    def __init__(
        self,
        api_key: str | None = None,
        request_json: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        download: Callable[[str], tuple[bytes, str]] | None = None,
    ):
        self.api_key = api_key or os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")
        if not self.api_key:
            raise ImageProviderError("FAL_KEY or FAL_API_KEY is not configured for Qwen Image Layered.")
        self._request_json = request_json or self._post_json
        self._download = download or self._download_image

    def decompose(self, request: LayeredImageRequest) -> LayeredImageResult:
        if not request.image_bytes:
            raise ImageProviderError("Qwen Image Layered requires a non-empty source image.")
        if not 2 <= request.num_layers <= 8:
            raise ImageProviderError("Qwen Image Layered layer count must be between 2 and 8.")
        payload = {
            "image_url": self._data_uri(request.image_bytes, request.mime_type),
            "prompt": request.prompt,
            "num_layers": request.num_layers,
            "output_format": "png",
            "enable_safety_checker": True,
        }
        response = self._request_json(self.model, payload)
        files = response.get("images") or []
        images: list[tuple[bytes, str]] = []
        # Qwen returns its compositing stack in back-to-front order. The first
        # file is the background layer, so preserve every returned layer.
        for item in files:
            url = item.get("url") if isinstance(item, dict) else None
            if not url:
                continue
            images.append(self._download(url))
        if len(images) < 2:
            raise ImageProviderError("Qwen Image Layered returned fewer than two layer images.")
        return LayeredImageResult(
            images=images,
            request_id=response.get("request_id") or response.get("requestId"),
            usage={key: response[key] for key in ("seed", "timings", "has_nsfw_concepts") if key in response},
        )

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"https://fal.run/{endpoint}", data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Key {self.api_key}", "Content-Type": "application/json"}, method="POST",
        )
        try:
            with urlopen(request, timeout=300) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise ImageProviderError(f"FAL Qwen layered request failed ({exc.code}): {detail}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ImageProviderError(f"FAL Qwen layered request failed: {exc}") from exc

    @staticmethod
    def _download_image(image_url: str) -> tuple[bytes, str]:
        try:
            with urlopen(image_url, timeout=180) as response:
                return response.read(), response.headers.get_content_type() or "image/png"
        except (HTTPError, URLError, TimeoutError) as exc:
            raise ImageProviderError(f"Unable to download FAL layered image: {exc}") from exc

    @staticmethod
    def _data_uri(data: bytes, mime_type: str) -> str:
        return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"
