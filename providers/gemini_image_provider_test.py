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


def test_gemini_image_provider_interleaves_labeled_reference_parts():
    from providers.image_provider import ImageReference

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
    mock_response.response_id = "resp-456"
    mock_response.usage_metadata = None
    mock_client.models.generate_content.return_value = mock_response

    provider = GeminiImageProvider(client=mock_client)
    refs = [
        ImageReference(name="captain_nova.png", data=b"data1", mime_type="image/png", label="Captain Nova"),
        ImageReference(name="dr_aris.png", data=b"data2", mime_type="image/png", label="Dr. Aris"),
    ]
    result = provider.generate(
        ImageGenerationRequest(
            prompt="Captain Nova and Dr. Aris exploring a neon city",
            references=refs,
        )
    )

    assert result.image_bytes == b"image_bytes"
    call_kwargs = mock_client.models.generate_content.call_args.kwargs
    contents = call_kwargs["contents"]
    # Check that contents contains label text parts interleaved with image parts
    assert len(contents) == 5
    assert contents[0] == "Reference image for 'Captain Nova':"
    assert contents[2] == "Reference image for 'Dr. Aris':"
    assert contents[4] == "Captain Nova and Dr. Aris exploring a neon city"

