from fastapi.testclient import TestClient

from testlab.music_benchmark import get_music_prompt, music_prompt_catalog
from providers.music_provider import MusicAudioArtifact, MusicGenerationResult
from testlab.server import app
from testlab.server import _estimated_music_output_cost
from testlab import server


def test_music_prompt_catalog():
    catalog = music_prompt_catalog()
    assert len(catalog) >= 6
    assert catalog[0]["id"] == "cinematic-orchestral"

    prompt = get_music_prompt("lofi-ambient")
    assert prompt.id == "lofi-ambient"
    assert prompt.genre == "Lofi Ambient"


def test_testlab_server_music_benchmark_routes():
    client = TestClient(app)

    response = client.get("/music-benchmark")
    assert response.status_code == 200
    assert "Music Provider Bench" in response.text

    catalog_response = client.get("/api/music-benchmark/catalog")
    assert catalog_response.status_code == 200
    data = catalog_response.json()
    assert "prompts" in data
    assert "providers" in data
    assert "adapters" in data
    assert any(p["id"] == "lyria" for p in data["providers"])
    assert any(adapter["id"] == "fal-stable-audio-3-base-a2a" for adapter in data["adapters"])


def test_music_adaptation_cost_includes_the_base_and_adapter():
    assert _estimated_music_output_cost(
        "test-base-plus-adapter",
        30,
        {"base_provider": "lyria", "adapter": "fal-stable-audio-3-base-a2a"},
    ) == 0.112


def test_composed_music_benchmark_saves_a_playable_base_artifact(monkeypatch, tmp_path):
    class ComposedProvider:
        def generate(self, request):
            return MusicGenerationResult(
                audio_bytes=b"adapted",
                mime_type="audio/mpeg",
                provider="test-base-plus-adapter",
                model="base -> adapter",
                artifacts={
                    "base": MusicAudioArtifact(
                        audio_bytes=b"base",
                        mime_type="audio/wav",
                        provider="base",
                        model="base-model",
                    )
                },
            )

    monkeypatch.setattr(server, "BENCHMARK_MUSIC_OUTPUT", tmp_path)
    monkeypatch.setattr(server, "get_music_provider", lambda *_: ComposedProvider())

    progress = []
    item = server._benchmark_one_music(
        "test-base-plus-adapter",
        get_music_prompt("lofi-ambient"),
        1,
        progress_callback=lambda stage, details: progress.append((stage, details)),
    )

    assert item["status"] == "completed"
    assert item["audio_url"].endswith(".mp3")
    assert item["base_audio_url"].endswith("_base.wav")
    assert item["base_model"] == "base-model"
    assert (tmp_path / item["audio_url"].rsplit("/", 1)[-1]).read_bytes() == b"adapted"
    assert (tmp_path / item["base_audio_url"].rsplit("/", 1)[-1]).read_bytes() == b"base"
    assert progress[0][0] == "submitted"
    assert progress[-1][0] == "completed"


def test_music_benchmark_custom_prompt_run(monkeypatch):
    monkeypatch.setattr(server, "_run_music_benchmark", lambda *args, **kwargs: None)
    client = TestClient(app)
    response = client.post(
        "/api/music-benchmark/runs",
        json={
            "provider_ids": ["lyria"],
            "custom_prompts": [
                {
                    "prompt": "Custom dark synth groove",
                    "genre": "Synthwave",
                    "duration_seconds": 20,
                }
            ],
            "repetitions": 1,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["prompts"]) == 1
    assert data["prompts"][0]["title"] == "Custom Music"
    assert data["prompts"][0]["prompt"] == "Custom dark synth groove"
    assert data["prompts"][0]["genre"] == "Synthwave"

