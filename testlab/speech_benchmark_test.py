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
