from fastapi.testclient import TestClient

from testlab.animation_benchmark import animation_prompt_catalog, get_animation_prompt
from testlab.server import app


def test_animation_benchmark_catalog_has_default_prompts():
    assert {item["id"] for item in animation_prompt_catalog()} >= {"forest-path", "coastal-light", "mountain-bridge"}
    assert "forest" in get_animation_prompt("forest-path").prompt


def test_animation_benchmark_routes():
    client = TestClient(app)
    assert client.get("/animation-benchmark").status_code == 200
    catalog = client.get("/api/animation-benchmark/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["prompts"][0]["id"] == "forest-path"


def test_animation_benchmark_validates_required_input():
    response = TestClient(app).post("/api/animation-benchmark/runs", json={})
    assert response.status_code == 400
