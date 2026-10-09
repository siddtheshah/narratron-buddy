from fastapi.testclient import TestClient

from testlab.image_benchmark import get_prompt, prompt_catalog
from testlab.server import app, _estimated_output_cost
from testlab import server


def test_image_prompt_catalog():
    catalog = prompt_catalog()
    assert len(catalog) >= 14
    assert catalog[0]["id"] == "cinematic-scene"

    prompt = get_prompt("cinematic-scene")
    assert prompt.id == "cinematic-scene"
    assert "cinematic storybook illustration" in prompt.prompt

    worm_prompt = get_prompt("dynamic-worm-angle")
    assert worm_prompt.dimension == "Dynamic perspective"
    assert "worm's-eye" in worm_prompt.prompt

    tiered_prompt = get_prompt("tiered-spatial-placement")
    assert tiered_prompt.dimension == "Relative placement"
    assert "mezzanine" in tiered_prompt.prompt

    aerial_prompt = get_prompt("aerial-chase-layout")
    assert aerial_prompt.dimension == "Dynamic perspective"

    depth_prompt = get_prompt("depth-occlusion-stack")
    assert "relative depth" in depth_prompt.prompt


def test_testlab_server_image_benchmark_routes():
    client = TestClient(app)

    response = client.get("/image-benchmark")
    assert response.status_code == 200
    assert "Image Provider Bench" in response.text

    catalog_response = client.get("/api/image-benchmark/catalog")
    assert catalog_response.status_code == 200
    data = catalog_response.json()
    assert "prompts" in data
    assert "providers" in data
    assert any(p["id"] == "gemini" for p in data["providers"])
    assert any(p["id"] == "hybrid-flux-gemini" for p in data["providers"])

    flare_spec = next(p for p in data["providers"] if p["id"] == "openai-gpt-image-flare")
    assert "gpt-image-2.5-sunburst" in flare_spec["model_options"]
    assert "gpt-image-2.5-flare" in flare_spec["model_options"]

    gpt_spec = next(p for p in data["providers"] if p["id"] == "openai-gpt-image")
    assert gpt_spec["model"] == "gpt-image-2"
    assert "gpt-image-2" in gpt_spec["model_options"]
    assert "gpt-image-1-mini" in gpt_spec["model_options"]


def test_image_benchmark_cost_calculation():
    assert _estimated_output_cost("gemini", 1.0) == 0.0336
    assert _estimated_output_cost("gemini", None) is None


def test_image_benchmark_custom_prompt_run(monkeypatch):
    monkeypatch.setattr(server, "_run_benchmark", lambda *args, **kwargs: None)
    client = TestClient(app)
    response = client.post(
        "/api/image-benchmark/runs",
        json={
            "provider_ids": ["gemini"],
            "custom_prompts": [
                {
                    "prompt": "An ancient stone lighthouse surrounded by glowing fireflies at twilight.",
                }
            ],
            "repetitions": 1,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["prompts"]) == 1
    assert data["prompts"][0]["title"] == "Custom Prompt"
    assert data["prompts"][0]["prompt"] == "An ancient stone lighthouse surrounded by glowing fireflies at twilight."


def test_image_benchmark_validation_requires_prompt_or_custom():
    client = TestClient(app)
    response = client.post(
        "/api/image-benchmark/runs",
        json={
            "provider_ids": ["gemini"],
            "prompt_ids": [],
            "custom_prompts": [],
            "repetitions": 1,
        },
    )
    assert response.status_code == 400
