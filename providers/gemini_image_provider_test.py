from unittest.mock import patch

from providers.gemini_image_provider import GeminiImageProvider


def test_gemini_image_provider_uses_developer_api_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    with patch("providers.gemini_image_provider.genai.Client") as client:
        GeminiImageProvider()

    client.assert_called_once_with(api_key="test-api-key")
