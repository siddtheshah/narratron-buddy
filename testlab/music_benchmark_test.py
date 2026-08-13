from fastapi.testclient import TestClient

from testlab.music_benchmark import get_music_prompt, music_prompt_catalog
from testlab.server import app


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
    assert any(p["id"] == "lyria" for p in data["providers"])
