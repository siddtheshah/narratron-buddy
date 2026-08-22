import pytest
from unittest.mock import patch

from providers.gemini_text_response_provider import GeminiTextResponseProvider
from providers.text_response_provider import (
    TextResponseAttachment,
    TextResponseProviderError,
    TextResponseRequest,
)
from providers.registry import (
    get_text_response_provider,
    list_text_response_provider_specs,
)


class DummyCandidate:
    def __init__(self, text="The adventure begins...", finish_reason="STOP"):
        self.content = {"parts": [{"text": text}]}
        self.finish_reason = finish_reason


class DummyTextResponse:
    def __init__(self, text="The adventure begins...", finish_reason="STOP"):
        self.text = text
        self.response_id = "gemini_txt_123"
        self.candidates = [DummyCandidate(text=text, finish_reason=finish_reason)]
        self.usage_metadata = {"prompt_token_count": 10, "candidates_token_count": 20}


class DummyModels:
    def __init__(self, should_fail=False, return_empty=False):
        self.should_fail = should_fail
        self.return_empty = return_empty
        self.last_kwargs = None

    def generate_content(self, **kwargs):
        if self.should_fail:
            raise Exception("API rate limit exceeded")
        self.last_kwargs = kwargs
        if self.return_empty:
            return DummyTextResponse(text=None, finish_reason="SAFETY")
        return DummyTextResponse()


class DummyClient:
    def __init__(self, should_fail=False, return_empty=False):
        self.models = DummyModels(should_fail=should_fail, return_empty=return_empty)


def test_text_response_request_validation():
    with pytest.raises(ValueError, match="Text response prompt cannot be empty"):
        TextResponseRequest(prompt="")

    with pytest.raises(ValueError, match="Temperature must be between 0.0 and 2.0"):
        TextResponseRequest(prompt="Hello", temperature=3.0)

    with pytest.raises(ValueError, match="max_output_tokens must be positive"):
        TextResponseRequest(prompt="Hello", max_output_tokens=0)


def test_gemini_text_provider_requires_credentials(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_PROJECT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    with pytest.raises(TextResponseProviderError, match="are not configured for Gemini Text Response"):
        GeminiTextResponseProvider()


def test_gemini_text_provider_uses_vertexai_client(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-vertex-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    with patch("providers.gemini_text_response_provider.genai.Client") as mock_client:
        GeminiTextResponseProvider()
    mock_client.assert_called_once_with(vertexai=True, location="us-central1", project="test-vertex-project")



def test_gemini_text_provider_generates_text_with_mock_client():
    client = DummyClient()
    provider = GeminiTextResponseProvider(client=client)
    req = TextResponseRequest(
        prompt="Describe the dark forest",
        system_instruction="You are a dungeon master",
        temperature=0.7,
        max_output_tokens=100,
    )

    result = provider.generate(req)

    assert client.models.last_kwargs["model"] == "gemini-2.5-flash-lite"
    assert client.models.last_kwargs["contents"] == "Describe the dark forest"
    config = client.models.last_kwargs["config"]
    assert config.system_instruction == "You are a dungeon master"
    assert config.temperature == 0.7
    assert config.max_output_tokens == 100

    assert result.text == "The adventure begins..."
    assert result.provider == "gemini-2-5"
    assert result.model == "gemini-2.5-flash-lite"
    assert result.request_id == "gemini_txt_123"
    assert result.finish_reason == "STOP"
    assert result.usage["prompt_token_count"] == 10


def test_gemini_text_provider_handles_client_exception():
    client = DummyClient(should_fail=True)
    provider = GeminiTextResponseProvider(client=client)
    req = TextResponseRequest(prompt="Hello")

    with pytest.raises(TextResponseProviderError, match="Gemini text request failed: API rate limit exceeded"):
        provider.generate(req)


def test_gemini_text_provider_handles_empty_response():
    client = DummyClient(return_empty=True)
    provider = GeminiTextResponseProvider(client=client)
    req = TextResponseRequest(prompt="Hello")

    with pytest.raises(TextResponseProviderError, match="Gemini returned no text response"):
        provider.generate(req)


def test_text_response_provider_registry(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    specs = list_text_response_provider_specs()
    assert any(spec["id"] == "gemini-2-5" and spec["status"] == "available" for spec in specs)
    assert any(spec["id"] == "gemini-3" and spec["status"] == "available" for spec in specs)

    provider = get_text_response_provider("gemini-2-5", options={"model": "gemini-2.5-pro"})
    assert provider.model == "gemini-2.5-pro"
    assert provider.id == "gemini-2-5"

    provider_3 = get_text_response_provider("gemini-3")
    assert provider_3.model == "gemini-3.6-flash"
    assert provider_3.id == "gemini-3"


    with pytest.raises(TextResponseProviderError, match="Unknown text response provider: invalid_id"):
        get_text_response_provider("invalid_id")


from pydantic import BaseModel, Field


class AdventureSummary(BaseModel):
    title: str
    danger_level: int = 1
    tags: list[str] = Field(default_factory=list)


def test_gemini_text_provider_structured_schema_success():
    client = DummyClient()

    def _generate_content(**kwargs):
        client.models.last_kwargs = kwargs
        return DummyTextResponse(
            text='{"title": "Dragon Cave", "danger_level": 5, "tags": ["dragon", "fire"]}'
        )

    client.models.generate_content = _generate_content
    provider = GeminiTextResponseProvider(client=client)

    req = TextResponseRequest(
        prompt="Generate an adventure summary",
        response_schema=AdventureSummary,
    )
    result = provider.generate(req)

    config = client.models.last_kwargs["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema == AdventureSummary

    assert isinstance(result.parsed, AdventureSummary)
    assert result.parsed.title == "Dragon Cave"
    assert result.parsed.danger_level == 5
    assert result.parsed.tags == ["dragon", "fire"]


def test_gemini_text_provider_supports_catalog_json_schema_attachments_and_model_override():
    client = DummyClient()
    client.models.generate_content = lambda **kwargs: (
        setattr(client.models, "last_kwargs", kwargs)
        or DummyTextResponse(text='{"answer": "look at the image"}')
    )
    provider = GeminiTextResponseProvider(client=client)
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }

    result = provider.generate(TextResponseRequest(
        prompt="Describe this image",
        model="gemini-3.7-flash",
        response_json_schema=schema,
        attachments=(TextResponseAttachment(data=b"image-bytes", mime_type="image/png"),),
    ))

    kwargs = client.models.last_kwargs
    assert kwargs["model"] == "gemini-3.7-flash"
    assert kwargs["contents"][0] == "Describe this image"
    assert kwargs["contents"][1].inline_data.mime_type == "image/png"
    assert kwargs["contents"][1].inline_data.data == b"image-bytes"
    config = kwargs["config"]
    assert config.response_json_schema == schema
    assert config.response_schema is None
    assert result.model == "gemini-3.7-flash"
    assert result.parsed == {"answer": "look at the image"}



def test_gemini_text_provider_structured_schema_validation_error():
    client = DummyClient()
    client.models.generate_content = lambda **kwargs: DummyTextResponse(
        text='{"invalid_json": true, "missing_title": 123}'
    )
    provider = GeminiTextResponseProvider(client=client)

    req = TextResponseRequest(
        prompt="Generate an adventure summary",
        response_schema=AdventureSummary,
    )
    with pytest.raises(TextResponseProviderError, match="Response failed schema validation for AdventureSummary"):
        provider.generate(req)


def test_gemini_text_provider_structured_schema_invalid_json():
    client = DummyClient()
    client.models.generate_content = lambda **kwargs: DummyTextResponse(
        text="Not valid JSON at all!"
    )
    provider = GeminiTextResponseProvider(client=client)

    req = TextResponseRequest(
        prompt="Generate an adventure summary",
        response_schema=AdventureSummary,
    )
    with pytest.raises(TextResponseProviderError, match="Response is not valid JSON for structured schema"):
        provider.generate(req)




