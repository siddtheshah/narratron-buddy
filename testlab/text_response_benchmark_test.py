from fastapi.testclient import TestClient

from testlab.text_response_benchmark import get_text_prompt, text_prompt_catalog
from testlab.server import app


def test_text_prompt_catalog():
    catalog = text_prompt_catalog()
    assert len(catalog) >= 6
    assert catalog[0]["id"] == "dm-adventure-intro"

    prompt = get_text_prompt("player-choice-resolution")
    assert prompt.id == "player-choice-resolution"
    assert prompt.temperature == 0.8


def test_testlab_server_text_benchmark_routes():
    client = TestClient(app)

    response = client.get("/text-benchmark")
    assert response.status_code == 200
    assert "Text Response Provider Bench" in response.text

    catalog_response = client.get("/api/text-benchmark/catalog")
    assert catalog_response.status_code == 200
    data = catalog_response.json()
    assert "prompts" in data
    assert "providers" in data
    assert any(p["id"] == "gemini-2-5" for p in data["providers"])
    assert any(p["id"] == "gemini-3" for p in data["providers"])


