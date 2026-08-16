"""FAL implementation for FLUX.2 Klein image generation and editing."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from providers.image_provider import ImageGenerationRequest, ImageGenerationResult, ImageProvider, ImageProviderError


class FalFluxKleinProvider(ImageProvider):
    """Use FAL's synchronous FLUX.2 Klein 9B endpoints.

    FAL exposes separate text-to-image and image-edit endpoints.  References
    are sent as data URIs to keep the benchmark self-contained and avoid
    uploading test images to a separate host first.
    """

    id = "flux-klein"
    display_name = "FLUX.2 Klein 9B (FAL)"
    model = "fal-ai/flux-2/klein/9b"
    edit_model = "fal-ai/flux-2/klein/9b/edit"

    def __init__(
        self,
        api_key: str | None = None,
        request_json: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        download: Callable[[str], tuple[bytes, str]] | None = None,
    ):
        self.api_key = api_key or os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY")
        if not self.api_key:
            raise ImageProviderError("FAL_KEY or FAL_API_KEY is not configured.")
        self._request_json = request_json or self._post_json
        self._download = download or self._download_image

    def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        if len(request.references) > 4:
            raise ImageProviderError("FLUX.2 Klein 9B on FAL accepts at most four reference images.")
        endpoint = self.edit_model if request.references else self.model
        payload: dict[str, Any] = {
            "prompt": request.prompt,
            "num_images": 1,
            "output_format": "png",
        }
        if request.width and request.height:
            payload["image_size"] = {"width": request.width, "height": request.height}
        else:
            payload["image_size"] = self._fal_image_size(request.resolved_aspect_ratio)
        if request.references:
            payload["image_urls"] = [self._data_uri(reference.data, reference.mime_type) for reference in request.references]

        response = self._request_json(endpoint, payload)
        images = response.get("images") or []
        image = images[0] if images else None
        image_url = image.get("url") if isinstance(image, dict) else None
        if not image_url:
            raise ImageProviderError("FAL returned no generated image URL.")
        image_bytes, mime_type = self._download(image_url)
        return ImageGenerationResult(
            image_bytes=image_bytes,
            mime_type=mime_type,
            provider=self.id,
            model=endpoint,
            request_id=response.get("request_id") or response.get("requestId"),
            usage={key: response[key] for key in ("seed", "timings", "has_nsfw_concepts") if key in response},
        )

    @staticmethod
    def _fal_image_size(aspect_ratio: str) -> str | dict[str, int]:
        mapping: dict[str, str | dict[str, int]] = {
            "16:9": "landscape_16_9",
            "4:3": "landscape_4_3",
            "1:1": "square_hd",
            "9:16": "portrait_16_9",
            "3:4": "portrait_4_3",
            "3:2": {"width": 1536, "height": 1024},
            "2:3": {"width": 1024, "height": 1536},
            "21:9": {"width": 1536, "height": 658},
        }
        return mapping.get(aspect_ratio, "landscape_16_9")

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"https://fal.run/{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Key {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=180) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise ImageProviderError(f"FAL image request failed ({exc.code}): {detail}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ImageProviderError(f"FAL image request failed: {exc}") from exc

    @staticmethod
    def _download_image(image_url: str) -> tuple[bytes, str]:
        try:
            with urlopen(image_url, timeout=180) as response:
                mime_type = response.headers.get_content_type() or "image/png"
                return response.read(), mime_type
        except (HTTPError, URLError, TimeoutError) as exc:
            raise ImageProviderError(f"Unable to download FAL generated image: {exc}") from exc

    @staticmethod
    def _data_uri(data: bytes, mime_type: str) -> str:
        return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"
