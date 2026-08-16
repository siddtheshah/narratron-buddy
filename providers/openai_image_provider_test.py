import base64
from unittest.mock import MagicMock, patch

from providers.image_provider import ImageGenerationRequest, ImageReference
from providers.openai_image_provider import OpenAIImageProvider


def test_openai_image_provider_uses_landscape_by_default():
    mock_client = MagicMock()
    mock_item = MagicMock()
    mock_item.b64_json = base64.b64encode(b"openai_image_bytes").decode("ascii")
    mock_response = MagicMock()
    mock_response.data = [mock_item]
    mock_response._request_id = "req-1"
    mock_response.usage = None
    mock_client.images.generate.return_value = mock_response

    provider = OpenAIImageProvider(client=mock_client)
    result = provider.generate(ImageGenerationRequest(prompt="a sunset over dunes"))

    assert result.image_bytes == b"openai_image_bytes"
    mock_client.images.generate.assert_called_once()
    kwargs = mock_client.images.generate.call_args.kwargs
    assert kwargs["size"] == "1536x1024"


def test_openai_image_provider_respects_square_and_portrait_aspect_ratio():
    mock_client = MagicMock()
    mock_item = MagicMock()
    mock_item.b64_json = base64.b64encode(b"img").decode("ascii")
    mock_response = MagicMock()
    mock_response.data = [mock_item]
    mock_response._request_id = "req-2"
    mock_response.usage = None
    mock_client.images.generate.return_value = mock_response

    provider = OpenAIImageProvider(client=mock_client)

    provider.generate(ImageGenerationRequest(prompt="square icon", aspect_ratio="1:1"))
    assert mock_client.images.generate.call_args.kwargs["size"] == "1024x1024"

    provider.generate(ImageGenerationRequest(prompt="portrait poster", aspect_ratio="9:16"))
    assert mock_client.images.generate.call_args.kwargs["size"] == "1024x1536"
