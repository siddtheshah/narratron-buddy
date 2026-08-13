import pytest

from providers.lyria_music_provider import LyriaMusicProvider
from providers.music_provider import MusicGenerationRequest, MusicProviderError
from providers.registry import get_music_provider, list_music_provider_specs


class DummyAudioResponse:
    def __init__(self, audio_bytes=b"sample_audio_data", mime_type="audio/mp3"):
        self.audio_bytes = audio_bytes
        self.mime_type = mime_type
        self.response_id = "lyria_resp_999"
        self.usage_metadata = {"duration": 30}


class DummyClient:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.last_prompt = None
        self.last_model = None

    def generate_audio(self, model: str, prompt: str):
        if self.should_fail:
            raise Exception("API connection timed out")
        self.last_model = model
        self.last_prompt = prompt
        return DummyAudioResponse()


def test_lyria_provider_requires_api_key(monkeypatch):
    monkeypatch.delenv("LYRIA_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(MusicProviderError, match="is not configured for Lyria 3"):
        LyriaMusicProvider()


def test_lyria_provider_generates_audio_with_mock_client():
    client = DummyClient()
    provider = LyriaMusicProvider(client=client)
    req = MusicGenerationRequest(prompt="A mystical forest", tempo="Presto", genre="Folk")

    result = provider.generate(req)

    assert client.last_model == "lyria-3-pro-preview"
    assert "A mystical forest" in client.last_prompt
    assert "Genre: Folk" in client.last_prompt
    assert "Tempo: Presto" in client.last_prompt
    assert result.audio_bytes == b"sample_audio_data"
    assert result.mime_type == "audio/mp3"
    assert result.provider == "lyria"
    assert result.model == "lyria-3-pro-preview"
    assert result.request_id == "lyria_resp_999"


def test_lyria_provider_handles_client_exception():
    client = DummyClient(should_fail=True)
    provider = LyriaMusicProvider(model="lyria-3-pro-preview", client=client)
    req = MusicGenerationRequest(prompt="A stormy sea")

    with pytest.raises(MusicProviderError, match="Lyria request failed: API connection timed out"):
        provider.generate(req)


def test_music_provider_registry():
    specs = list_music_provider_specs()
    assert any(spec["id"] == "lyria" for spec in specs)
    assert any(spec["id"] == "seedance" for spec in specs)

    provider = get_music_provider("lyria", options={"model": "lyria-3-pro"})
    assert provider.model == "lyria-3-pro"
    assert provider.id == "lyria"

    with pytest.raises(MusicProviderError, match="Seedance Music 1.0 is listed for comparison but its adapter is not configured yet."):
        get_music_provider("seedance")

    with pytest.raises(MusicProviderError, match="Unknown music provider: invalid_id"):
        get_music_provider("invalid_id")
