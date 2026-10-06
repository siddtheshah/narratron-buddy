"""Flare requests use quality rendering and explicit alpha-capable PNG output."""

import base64
from unittest.mock import MagicMock, patch

from openai import OpenAI
from openai.types.images_response import ImagesResponse
import pytest

from providers.image_provider import ImageGenerationRequest, ImageProviderError, ImageReference
from providers.openai_flare_image_provider import OpenAIFlareImageProvider
from providers.registry import get_image_provider, list_image_provider_specs


def image_client(content: bytes = b"png-image") -> MagicMock:
    client = MagicMock(spec=OpenAI)
    response = ImagesResponse(
        created=1, data=[{"b64_json": base64.b64encode(content).decode("ascii")}],
        usage={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30,
               "input_tokens_details": {"image_tokens": 0, "text_tokens": 10}},
    )
    response._request_id = "flare-request"
    client.images.generate.return_value = response
    client.images.edit.return_value = response
    return client


def test_flare_generates_high_quality_transparent_square_png() -> None:
    client = image_client()
    result = OpenAIFlareImageProvider(client=client).generate(
        ImageGenerationRequest(prompt="Goblin", aspect_ratio="1:1", background="transparent")
    )
    client.images.generate.assert_called_once_with(
        model="gpt-image-2.5-flare", prompt="Goblin", size="1024x1024",
        quality="high", n=1, output_format="png", background="transparent",
    )
    client.images.edit.assert_not_called()
    assert result.image_bytes == b"png-image"
    assert result.provider == "openai-gpt-image-flare"
    assert result.mime_type == "image/png"
    assert result.request_id == "flare-request"
    assert result.usage["total_tokens"] == 30


def test_flare_references_use_edit_endpoint_and_keep_background_choice() -> None:
    client = image_client()
    reference = ImageReference(name="hero.png", data=b"reference", mime_type="image/png")
    OpenAIFlareImageProvider(client=client).generate(
        ImageGenerationRequest(prompt="Hero in the harbor", references=[reference], background="opaque")
    )
    client.images.generate.assert_not_called()
    params = client.images.edit.call_args.kwargs
    assert params["model"] == "gpt-image-2.5-flare"
    assert params["size"] == "1536x864"
    assert params["background"] == "opaque"
    assert params["image"][0][0] == "hero.png"
    assert params["image"][0][1].getvalue() == b"reference"
    assert params["image"][0][2] == "image/png"


@pytest.mark.parametrize("content", ["", "not base64!"])
def test_flare_rejects_empty_or_invalid_base64(content: str) -> None:
    client = image_client()
    client.images.generate.return_value = ImagesResponse(created=1, data=[{"b64_json": content}])
    with pytest.raises(ImageProviderError):
        OpenAIFlareImageProvider(client=client).generate(ImageGenerationRequest(prompt="Hero"))


def test_flare_normalizes_api_failures() -> None:
    client = image_client()
    client.images.generate.side_effect = RuntimeError("provider unavailable")
    with pytest.raises(ImageProviderError, match="OpenAI Flare image request failed"):
        OpenAIFlareImageProvider(client=client).generate(ImageGenerationRequest(prompt="Hero"))


def test_flare_catalog_and_resolution_are_separate_from_live_provider() -> None:
    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch(
        "providers.openai_flare_image_provider.OpenAI", return_value=image_client(),
    ) as factory:
        provider = get_image_provider("openai-gpt-image-flare")
        assert provider.model == "gpt-image-2.5-flare"
        factory.assert_called_once_with(api_key="test-key", timeout=300.0, max_retries=0)
        spec = next(item for item in list_image_provider_specs() if item["id"] == provider.id)
        assert spec["status"] == "available"
    with pytest.raises(ImageProviderError, match="Unsupported Flare quality"):
        get_image_provider("openai-gpt-image-flare", {"quality": "invalid"})


def test_flare_custom_dimensions_validate_before_api_call() -> None:
    client = image_client()
    provider = OpenAIFlareImageProvider(client=client)
    provider.generate(ImageGenerationRequest(prompt="Hero", width=1536, height=864))
    assert client.images.generate.call_args.kwargs["size"] == "1536x864"
    client.images.generate.reset_mock()
    with pytest.raises(ImageProviderError, match="dimensions"):
        provider.generate(ImageGenerationRequest(prompt="Hero", width=257, height=257))
    client.images.generate.assert_not_called()
