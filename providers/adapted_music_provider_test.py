from providers.adapted_music_provider import AdaptedMusicProvider
from providers.music_provider import MusicGenerationRequest, MusicGenerationResult


class FakeBase:
    id = "another-music-provider"
    model = "base-model"

    def generate(self, request):
        return MusicGenerationResult(b"source", "audio/wav", self.id, self.model, request_id="base-request")


class FakeAdapter:
    id = "another-audio-adapter"
    model = "adapter-model"

    def adapt(self, request):
        assert request.source_audio == b"source"
        assert request.source_mime_type == "audio/wav"
        return MusicGenerationResult(b"adapted", "audio/mpeg", self.id, self.model, request_id="adapter-request")


def test_adapted_music_provider_composes_any_base_with_any_adapter():
    provider = AdaptedMusicProvider(FakeBase(), FakeAdapter())
    stages = []
    result = provider.generate(MusicGenerationRequest(prompt="Change the mood", on_progress=lambda stage, details: stages.append((stage, details))))

    assert result.audio_bytes == b"adapted"
    assert result.model == "base-model -> adapter-model"
    assert result.artifacts["base"].audio_bytes == b"source"
    assert result.artifacts["base"].model == "base-model"
    assert [stage for stage, _ in stages] == ["base_generating", "base_completed", "adapter_generating", "adapter_completed"]
    assert result.usage["timings"]["base_latency_ms"] >= 0
    assert result.usage["timings"]["adapter_latency_ms"] >= 0
    assert result.usage["base"]["provider"] == "another-music-provider"
    assert result.usage["adapter"]["provider"] == "another-audio-adapter"
