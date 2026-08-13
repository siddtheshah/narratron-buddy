import pytest

from providers.music_provider import (
    MusicGenerationRequest,
    MusicGenerationResult,
)


def test_music_generation_request_defaults_and_validation():
    req = MusicGenerationRequest(prompt="Epic orchestral scene")
    assert req.prompt == "Epic orchestral scene"
    assert req.duration_seconds == 30.0
    assert req.count == 1
    assert req.tempo is None
    assert req.genre is None


def test_music_generation_request_invalid_count():
    with pytest.raises(ValueError, match="Narratron music requests must request exactly one output track."):
        MusicGenerationRequest(prompt="Test", count=2)


def test_music_generation_request_invalid_duration():
    with pytest.raises(ValueError, match="Duration must be a positive number of seconds."):
        MusicGenerationRequest(prompt="Test", duration_seconds=-5.0)


def test_music_generation_result_dataclass():
    res = MusicGenerationResult(
        audio_bytes=b"dummy_audio_bytes",
        mime_type="audio/mp3",
        provider="lyria",
        model="lyria-3",
        request_id="req_123",
        usage={"tokens": 100},
    )
    assert res.audio_bytes == b"dummy_audio_bytes"
    assert res.mime_type == "audio/mp3"
    assert res.provider == "lyria"
    assert res.model == "lyria-3"
    assert res.request_id == "req_123"
    assert res.usage == {"tokens": 100}
