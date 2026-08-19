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


def test_text_benchmark_custom_prompt_run(monkeypatch):
    from testlab import server
    monkeypatch.setattr(server, "_run_text_benchmark", lambda *args, **kwargs: None)
    client = TestClient(app)
    response = client.post(
        "/api/text-benchmark/runs",
        json={
            "provider_ids": ["gemini-3"],
            "custom_prompts": [
                {
                    "prompt": "Describe a hidden clockwork city.",
                    "system_instruction": "You are a master narrator.",
                    "temperature": 0.5,
                    "max_output_tokens": 800,
                }
            ],
            "repetitions": 1,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["prompts"]) == 1
    assert data["prompts"][0]["title"] == "Custom Text Prompt"
    assert data["prompts"][0]["prompt"] == "Describe a hidden clockwork city."
    assert data["prompts"][0]["system_instruction"] == "You are a master narrator."
    assert data["prompts"][0]["temperature"] == 0.5



