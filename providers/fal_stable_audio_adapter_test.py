import base64
import json

from providers.fal_stable_audio_adapter import FalStableAudioAdapter
from providers.music_provider import MusicAdaptationRequest


class FakeResponse:
    def __init__(self, body: bytes, mime_type: str = "audio/mpeg"):
        self._body = body
        self.headers = type("Headers", (), {"get_content_type": lambda self: mime_type})()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_fal_stable_audio_adapts_a_provider_neutral_source_track():
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        if isinstance(request, str):
            return FakeResponse(b"adapted-audio")
        return FakeResponse(json.dumps({"audio": {"url": "https://example.test/result.mp3", "content_type": "audio/mpeg", "file_size": 13}, "seed": 42}).encode())

    provider = FalStableAudioAdapter(api_key="test-key", urlopen_fn=fake_urlopen)
    result = provider.adapt(MusicAdaptationRequest(source_audio=b"base-audio", source_mime_type="audio/wav", prompt="Make it warmer", duration_seconds=15))

    payload = json.loads(calls[0][0].data.decode())
    assert calls[0][0].full_url.endswith("fal-ai/stable-audio-3/small/music/base/audio-to-audio")
    assert payload["audio_url"] == "data:audio/wav;base64," + base64.b64encode(b"base-audio").decode()
    assert payload["prompt"] == "Make it warmer"
    assert payload["duration"] == 15
    assert result.audio_bytes == b"adapted-audio"
    assert result.mime_type == "audio/mpeg"
    assert result.provider == "fal-stable-audio-3-base-a2a"
    assert result.usage["seed"] == 42
