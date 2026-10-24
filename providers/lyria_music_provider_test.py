import base64

import pytest

from providers.lyria_music_provider import LyriaMusicProvider
from providers.music_provider import MusicGenerationRequest, MusicProviderError
from providers.registry import get_music_provider, list_music_provider_specs


class DummyAudio:
    def __init__(self, data: bytes | str = b"sample_audio_data", mime_type: str | None = "audio/mp3") -> None:
        self.data = data
        self.mime_type = mime_type


class DummyUsage:
    def model_dump(self, *, exclude_none: bool = False) -> dict[str, int]:
        return {"total_tokens": 120}


class DummyInteraction:
    def __init__(self, audio: DummyAudio | None = None) -> None:
        self.output_audio = audio or DummyAudio()
        self.id = "interaction_abc123"
        self.usage = DummyUsage()


class DummyInteractions:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.last_model: str | None = None
        self.last_input: str | None = None
        self.response = DummyInteraction()

    def create(self, *, model: str, input: str) -> DummyInteraction:
        if self.should_fail:
            raise RuntimeError("API connection timed out")
        self.last_model = model
        self.last_input = input
        return self.response


class DummyClient:
    def __init__(self, should_fail: bool = False) -> None:
        self.interactions = DummyInteractions(should_fail=should_fail)


def test_lyria_provider_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LYRIA_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    with pytest.raises(MusicProviderError, match="is not configured for Lyria 3"):
        LyriaMusicProvider()


@pytest.mark.parametrize("model", ["lyria-3.5", "lyria-3-pro-preview", "lyria-3-clip-preview"])
def test_lyria_models_use_shared_interactions_api(model: str) -> None:
    client = DummyClient()
    provider = LyriaMusicProvider(model=model, client=client)
    request = MusicGenerationRequest(prompt="A mystical forest", tempo="Presto", genre="Folk")

    result = provider.generate(request)

    assert client.interactions.last_model == model
    assert client.interactions.last_input is not None
    assert "A mystical forest" in client.interactions.last_input
    assert "Genre: Folk" in client.interactions.last_input
    assert "Tempo: Presto" in client.interactions.last_input
    assert "Target duration: 30.0 seconds" in client.interactions.last_input
    assert result.audio_bytes == b"sample_audio_data"
    assert result.mime_type == "audio/mp3"
    assert result.model == model
    assert result.request_id == "interaction_abc123"
    assert result.usage == {"total_tokens": 120}


def test_lyria_provider_decodes_documented_base64_audio() -> None:
    client = DummyClient()
    client.interactions.response = DummyInteraction(DummyAudio(base64.b64encode(b"decoded_audio").decode("ascii")))
    provider = LyriaMusicProvider(model="lyria-3.5", client=client)

    result = provider.generate(MusicGenerationRequest(prompt="Orchestral overture"))

    assert result.audio_bytes == b"decoded_audio"


def test_lyria_provider_reports_missing_audio() -> None:
    client = DummyClient()
    client.interactions.response = DummyInteraction()
    client.interactions.response.output_audio = None
    provider = LyriaMusicProvider(client=client)

    with pytest.raises(MusicProviderError, match="returned no binary audio"):
        provider.generate(MusicGenerationRequest(prompt="A stormy sea"))


def test_lyria_provider_handles_client_exception() -> None:
    provider = LyriaMusicProvider(client=DummyClient(should_fail=True))

    with pytest.raises(MusicProviderError, match="Lyria request failed: API connection timed out"):
        provider.generate(MusicGenerationRequest(prompt="A stormy sea"))


def test_music_provider_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    specs = list_music_provider_specs()
    assert any(spec["id"] == "lyria" for spec in specs)
    assert any(spec["id"] == "lyria-3.5" for spec in specs)
    assert any(spec["id"] == "seedance" for spec in specs)

    provider_35 = get_music_provider("lyria-3.5")
    assert provider_35.model == "lyria-3.5"
    assert provider_35.id == "lyria-3.5"
    assert provider_35.display_name == "Google Lyria 3.5"

    provider_custom = get_music_provider("lyria", options={"model": "lyria-3.5"})
    assert provider_custom.model == "lyria-3.5"
    assert provider_custom.id == "lyria"
    assert provider_custom.display_name == "Google Lyria 3.5"

    provider_pro = get_music_provider("lyria", options={"model": "lyria-3-pro"})
    assert provider_pro.model == "lyria-3-pro"
    assert provider_pro.id == "lyria"

    with pytest.raises(MusicProviderError, match="Seedance Music 1.0 is listed for comparison but its adapter is not configured yet."):
        get_music_provider("seedance")

    with pytest.raises(MusicProviderError, match="Unknown music provider: invalid_id"):
        get_music_provider("invalid_id")
