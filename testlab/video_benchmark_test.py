import time
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from providers.video_provider import VideoGenerationResult
from testlab.server import app
from testlab.video_benchmark import get_video_prompt, video_prompt_catalog


def test_video_benchmark_catalog_has_default_prompts():
    catalog = video_prompt_catalog()
    ids = {item["id"] for item in catalog}
    assert "dragon-flight" in ids
    assert "cyberpunk-pan" in ids
    assert "ocean-waves" in ids
    prompt = get_video_prompt("dragon-flight")
    assert "dragon" in prompt.prompt.lower()


def test_video_benchmark_routes():
    client = TestClient(app)
    assert client.get("/video-benchmark").status_code == 200
    assert client.get("/video-lab").status_code == 200

    catalog_resp = client.get("/api/video-benchmark/catalog")
    assert catalog_resp.status_code == 200
    data = catalog_resp.json()
    assert "prompts" in data
    assert "providers" in data
    assert any(p["id"] == "fal-minimax-h3-turbo" for p in data["providers"])


def test_video_benchmark_validates_required_input():
    client = TestClient(app)
    resp = client.post("/api/video-benchmark/runs", json={})
    assert resp.status_code == 400
    assert "prompt" in resp.json()["detail"].lower()


@patch("testlab.server.get_video_provider")
def test_video_benchmark_run_execution(mock_get_provider):
    mock_provider = MagicMock()
    mock_provider.id = "fal-minimax-h3-turbo"
    mock_provider.model = "fal-ai/minimax-h3-turbo/text-to-video"
    mock_provider.generate.return_value = VideoGenerationResult(
        video_bytes=b"fake-mp4-stream",
        mime_type="video/mp4",
        provider="fal-minimax-h3-turbo",
        model="fal-ai/minimax-h3-turbo/text-to-video",
        request_id="test-vid-req-123",
        usage={"timings": {"inference": 1.8}},
        video_url="https://fal.media/sample.mp4",
    )
    mock_get_provider.return_value = mock_provider

    client = TestClient(app)
    post_resp = client.post(
        "/api/video-benchmark/runs",
        json={
            "provider_id": "fal-minimax-h3-turbo",
            "prompt_id": "dragon-flight",
        },
    )
    assert post_resp.status_code == 200
    run_id = post_resp.json()["id"]

    # Poll run status
    max_retries = 30
    final_data = None
    for _ in range(max_retries):
        poll_resp = client.get(f"/api/video-benchmark/runs/{run_id}")
        assert poll_resp.status_code == 200
        data = poll_resp.json()
        if data["status"] in ("completed", "failed"):
            final_data = data
            break
        time.sleep(0.1)

    assert final_data is not None
    assert final_data["status"] == "completed"
    assert "video_url" in final_data
    assert final_data["video_url"].endswith(".mp4")
    assert final_data["model"] == "fal-ai/minimax-h3-turbo/text-to-video"
