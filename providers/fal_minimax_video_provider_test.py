import pytest

from providers.fal_minimax_video_provider import FalMinimaxVideoProvider
from providers.video_provider import VideoGenerationRequest, VideoProviderError


def test_minimax_video_posts_prompt_and_downloads_video():
    calls = []

    def request_json(endpoint, payload):
        calls.append((endpoint, payload))
        return {
            "request_id": "req-video-1",
            "seed": 42,
            "timings": {"inference": 2.1},
            "video": {
                "url": "https://fal.media/files/monkey/sample.mp4",
                "content_type": "video/mp4",
                "file_size": 1048576,
            },
        }

    def download(url):
        assert url == "https://fal.media/files/monkey/sample.mp4"
        return b"fake-mp4-data", "video/mp4"

    provider = FalMinimaxVideoProvider(
        api_key="test-key",
        request_json=request_json,
        download=download,
    )
    result = provider.generate(VideoGenerationRequest(prompt="A knight riding a horse across a bridge at sunset."))

    assert len(calls) == 1
    assert calls[0][0] == "fal-ai/minimax-h3-turbo/text-to-video"
    assert calls[0][1]["prompt"] == "A knight riding a horse across a bridge at sunset."
    assert calls[0][1]["prompt_expansion_mode"] == "balanced"
    assert result.video_bytes == b"fake-mp4-data"
    assert result.mime_type == "video/mp4"
    assert result.provider == "fal-minimax-h3-turbo"
    assert result.model == "fal-ai/minimax-h3-turbo/text-to-video"
    assert result.request_id == "req-video-1"
    assert result.video_url == "https://fal.media/files/monkey/sample.mp4"
    assert result.usage.get("seed") == 42


def test_minimax_video_custom_expansion_mode():
    calls = []

    def request_json(endpoint, payload):
        calls.append((endpoint, payload))
        return {"video": {"url": "https://fal.media/video.mp4"}}

    provider = FalMinimaxVideoProvider(
        api_key="test-key",
        request_json=request_json,
        download=lambda url: (b"video", "video/mp4"),
    )
    provider.generate(
        VideoGenerationRequest(
            prompt="A futuristic train zooming through neon city.",
            prompt_expansion_mode="fast",
        )
    )

    assert calls[0][1]["prompt_expansion_mode"] == "fast"


def test_minimax_video_requires_prompt():
    provider = FalMinimaxVideoProvider(api_key="test-key")
    with pytest.raises(VideoProviderError, match="non-empty prompt"):
        provider.generate(VideoGenerationRequest(prompt="   "))


def test_minimax_video_requires_api_key(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("FAL_API_KEY", raising=False)
    with pytest.raises(VideoProviderError, match="FAL_KEY or FAL_API_KEY is not configured"):
        FalMinimaxVideoProvider(api_key=None)


def test_minimax_video_raises_when_no_video_url():
    provider = FalMinimaxVideoProvider(
        api_key="test-key",
        request_json=lambda endpoint, payload: {"error": "queue full"},
    )
    with pytest.raises(VideoProviderError, match="no generated video URL"):
        provider.generate(VideoGenerationRequest(prompt="A castle on a mountain."))


def test_minimax_video_passes_duration():
    calls = []

    def request_json(endpoint, payload):
        calls.append((endpoint, payload))
        return {"video": {"url": "https://fal.media/video.mp4"}}

    provider = FalMinimaxVideoProvider(
        api_key="test-key",
        request_json=request_json,
        download=lambda url: (b"video", "video/mp4"),
    )
    provider.generate(
        VideoGenerationRequest(
            prompt="A majestic eagle flying over snowy peaks.",
            video_duration_seconds=5,
        )
    )

    assert len(calls) == 1
    assert calls[0][1]["duration"] == 5

