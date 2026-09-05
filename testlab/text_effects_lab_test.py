from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from testlab.server import app


def test_text_effects_lab_page():
    client = TestClient(app)
    response = client.get("/text-effects")
    assert response.status_code == 200
    assert "Text Effects & Beautifier Demo" in response.text
    assert "splitting.min.js" in response.text
    assert "text-effects.css" in response.text


def test_text_effects_presets_endpoint():
    client = TestClient(app)
    response = client.get("/api/text-effects/presets")
    assert response.status_code == 200
    data = response.json()
    assert "presets" in data
    assert len(data["presets"]) >= 4
    preset_ids = [p["id"] for p in data["presets"]]
    assert "dragon" in preset_ids
    assert "crypt" in preset_ids


def test_text_effects_beautify_endpoint_validation():
    client = TestClient(app)
    # Both narration and dialogue empty should return 400
    response = client.post("/api/text-effects/beautify", json={"narration": "", "dialogue": []})
    assert response.status_code == 400


def test_text_effects_beautify_endpoint_success():
    client = TestClient(app)
    mock_beautified = {
        "narration": "The cavern floor fractures with a deafening CRACK!",
        "narration_spans": [
            {"text": "The cavern floor fractures with a ", "effect": "none", "font": "default", "color": None},
            {"text": "deafening CRACK!", "effect": "vibrate", "font": "bangers", "color": "#ef4444"},
        ],
        "dialogue": [
            {
                "speaker": "Theresa",
                "text": "Shields up!",
                "kind": "speech",
                "spans": [{"text": "Shields up!", "effect": "pulse", "font": "bangers", "color": "#f59e0b"}],
            }
        ],
    }

    with patch("testlab.server.TextBeautifier.beautify_scene", return_value=mock_beautified):
        response = client.post(
            "/api/text-effects/beautify",
            json={
                "narration": "The cavern floor fractures with a deafening CRACK!",
                "dialogue": [{"speaker": "Theresa", "text": "Shields up!", "kind": "speech"}],
                "model": "gemini-3.5-flash-lite",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["narration_spans"]) == 2
        assert data["narration_spans"][1]["effect"] == "vibrate"
        assert len(data["dialogue"]) == 1
        assert data["dialogue"][0]["spans"][0]["effect"] == "pulse"
        assert "latency_seconds" in data
