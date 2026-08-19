from fastapi.testclient import TestClient

from testlab.speech_benchmark import get_speech_prompt, speech_prompt_catalog
from testlab.server import app


def test_speech_prompt_catalog():
    assert len(speech_prompt_catalog()) >= 4
    assert get_speech_prompt("nervous-alchemist").dimension == "Character Performance"


def test_speech_benchmark_routes():
    client = TestClient(app)
    assert "Speech Provider Bench" in client.get("/speech-benchmark").text
    data = client.get("/api/speech-benchmark/catalog").json()
    assert any(provider["id"] == "gemini-flash-tts" for provider in data["providers"])
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

