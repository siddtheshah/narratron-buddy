from fastapi.testclient import TestClient

from providers.speech_provider import SpeechSynthesisResult
from testlab.speech_benchmark import get_speech_prompt, speech_prompt_catalog
from testlab.server import app


def test_speech_prompt_catalog():
    assert len(speech_prompt_catalog()) >= 4
    assert get_speech_prompt("nervous-alchemist").dimension == "Character Performance"


def test_speech_benchmark_routes():
    client = TestClient(app)
    page = client.get("/speech-benchmark").text
    assert "Speech Provider Bench" in page
    assert "Voice tags / filters (selects a voice)" in page
    assert "Selected voice:" in page
    data = client.get("/api/speech-benchmark/catalog").json()
    gemini = next(provider for provider in data["providers"] if provider["id"] == "gemini-flash-tts")
    assert gemini["model"] == "gemini-3.8-flash-tts"
    assert gemini["model_options"] == ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts"]
    assert any(provider["id"] == "fal-seed-speech" for provider in data["providers"])
    assert any(provider["id"] == "google-chirp-3-hd" for provider in data["providers"])


def test_speech_benchmark_custom_prompt_run(monkeypatch):
    from testlab import server
    monkeypatch.setattr(server, "_run_speech_benchmark", lambda *args, **kwargs: None)
    client = TestClient(app)
    response = client.post(
        "/api/speech-benchmark/runs",
        json={
            "provider_ids": ["gemini-flash-tts"],
            "custom_prompts": [
                {
                    "text": "The kingdom has fallen, yet hope remains.",
                    "voice_instruction": "Whisper with sorrow",
                }
            ],
            "repetitions": 1,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["prompts"]) == 1
    assert data["prompts"][0]["title"] == "Custom Dialogue"
    assert data["prompts"][0]["text"] == "The kingdom has fallen, yet hope remains."
    assert data["prompts"][0]["voice_instruction"] == "Whisper with sorrow"


def test_speech_benchmark_selects_a_voice_from_tags(monkeypatch, tmp_path):
    from testlab import server

    class Provider:
        def __init__(self):
            self.selected_tags = None
            self.request = None

        def select_voice(self, tags):
            self.selected_tags = tags
            return "voice_selected_from_tags"

        def synthesize(self, request):
            self.request = request
            return SpeechSynthesisResult(
                audio_bytes=b"wav",
                mime_type="audio/wav",
                provider="test",
                model="test-model",
            )

    provider = Provider()
    monkeypatch.setattr(server, "get_speech_provider", lambda *_: provider)
    monkeypatch.setattr(server, "BENCHMARK_SPEECH_OUTPUT", tmp_path)

    item = server._benchmark_one_speech(
        "gemini-flash-tts",
        get_speech_prompt("heroic-rally"),
        1,
        {"voice_tags": "female, adventurous"},
    )

    assert provider.selected_tags == {"voice_tags": ["female", "adventurous"]}
    assert provider.request.voice == "voice_selected_from_tags"
    assert item["selected_voice"] == "voice_selected_from_tags"
    assert item["voice_tags"] == ["female", "adventurous"]
