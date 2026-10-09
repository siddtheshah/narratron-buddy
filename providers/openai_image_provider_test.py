import base64
from unittest.mock import MagicMock

from providers.image_provider import ImageGenerationRequest
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
    assert result.model == "gpt-image-2"
    mock_client.images.generate.assert_called_once()
    kwargs = mock_client.images.generate.call_args.kwargs
    assert kwargs["size"] == "1536x1024"
    assert kwargs["model"] == "gpt-image-2"


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


def test_openai_image_provider_supports_model_selection():
    mock_client = MagicMock()
    mock_item = MagicMock()
    mock_item.b64_json = base64.b64encode(b"img").decode("ascii")
    mock_response = MagicMock()
    mock_response.data = [mock_item]
    mock_response._request_id = "req-3"
    mock_response.usage = None
    mock_client.images.generate.return_value = mock_response

    provider = OpenAIImageProvider(model="gpt-image-1-mini", client=mock_client)
    result = provider.generate(ImageGenerationRequest(prompt="quick sketch"))

    assert result.model == "gpt-image-1-mini"
    assert mock_client.images.generate.call_args.kwargs["model"] == "gpt-image-1-mini"


def test_registry_openai_image_resolution():
    from providers.registry import get_image_provider, list_image_provider_specs
    from unittest.mock import patch

    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch(
        "providers.openai_image_provider.OpenAI", return_value=MagicMock()
    ):
        provider_default = get_image_provider("openai-gpt-image")
        assert provider_default.model == "gpt-image-2"

        provider_mini = get_image_provider("openai-gpt-image", {"model": "gpt-image-1-mini"})
        assert provider_mini.model == "gpt-image-1-mini"

        spec = next(s for s in list_image_provider_specs() if s["id"] == "openai-gpt-image")
        assert spec["model"] == "gpt-image-2"
        assert "gpt-image-2" in spec["model_options"]
        assert "gpt-image-1-mini" in spec["model_options"]
