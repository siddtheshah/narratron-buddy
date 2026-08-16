from unittest.mock import MagicMock, patch

from providers.gemini_image_provider import GeminiImageProvider
from providers.image_provider import ImageGenerationRequest


def test_gemini_image_provider_uses_developer_api_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    with patch("providers.gemini_image_provider.genai.Client") as client:
        GeminiImageProvider()

    client.assert_called_once_with(api_key="test-api-key")


def test_gemini_image_provider_configures_aspect_ratio():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_candidate = MagicMock()
    mock_part = MagicMock()
    mock_inline = MagicMock()
    mock_inline.data = b"image_bytes"
    mock_inline.mime_type = "image/png"
    mock_part.inline_data = mock_inline
    mock_candidate.content.parts = [mock_part]
    mock_response.candidates = [mock_candidate]
    mock_response.response_id = "resp-123"
    mock_response.usage_metadata = None
    mock_client.models.generate_content.return_value = mock_response

    provider = GeminiImageProvider(client=mock_client)
    result = provider.generate(ImageGenerationRequest(prompt="cinematic mountain vista"))

    assert result.image_bytes == b"image_bytes"
    assert result.provider == "gemini"
    mock_client.models.generate_content.assert_called_once()
    call_kwargs = mock_client.models.generate_content.call_args.kwargs
    assert "config" in call_kwargs
    config = call_kwargs["config"]
    assert config.image_config.aspect_ratio == "16:9"
