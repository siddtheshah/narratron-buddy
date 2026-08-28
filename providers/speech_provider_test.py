import wave
from io import BytesIO

import pytest

from providers.fal_seed_speech_provider import FalSeedSpeechProvider
from providers.gemini_speech_provider import GeminiSpeechProvider
from providers.google_chirp_speech_provider import GoogleChirpSpeechProvider
from providers.speech_provider import SpeechSynthesisRequest


def test_speech_request_validation():
    with pytest.raises(ValueError, match="cannot be empty"):
        SpeechSynthesisRequest(text=" ")
    with pytest.raises(ValueError, match="speed"):
        SpeechSynthesisRequest(text="Hello", speed=0)


def test_gemini_speech_converts_pcm_to_wav():
    class Interactions:
        @staticmethod
        def create(**kwargs):
            assert kwargs["generation_config"]["speech_config"][0]["voice"] == "Kore"
            assert "voice_instruction" not in kwargs["generation_config"]["speech_config"][0]
            return {"output_audio": {"data": "AQACAAMABAA="}, "request_id": "gemini-request"}

    provider = GeminiSpeechProvider(client=type("Client", (), {"interactions": Interactions()})())
    result = provider.synthesize(SpeechSynthesisRequest(text="Hello.", voice_instruction="Speak warmly."))
    assert result.mime_type == "audio/wav"
    assert result.request_id == "gemini-request"
    with wave.open(BytesIO(result.audio_bytes)) as output:
        assert output.getframerate() == 24_000
        assert output.readframes(2) == b"\x01\x00\x02\x00"


def test_fal_seed_speech_uses_documented_payload_and_downloads_audio():
    calls = []

    def post(endpoint, payload):
        calls.append((endpoint, payload))
        return {"request_id": "fal-request", "audio": {"url": "https://audio.example/out.mp3"}}

    provider = FalSeedSpeechProvider(api_key="test", request_json=post, download=lambda _: (b"mp3", "audio/mpeg"))
    result = provider.synthesize(SpeechSynthesisRequest(text="A dramatic line.", voice="dacey_en", speed=1.1, voice_instruction="Speak gravely."))
    assert calls == [("fal-ai/bytedance/seed-speech/tts/v2", {"text": "A dramatic line.", "voice": "dacey_en", "output_format": "mp3", "sample_rate": 24000, "speed": 1.1, "voice_instruction": "Speak gravely."})]
    assert result.audio_bytes == b"mp3"
    assert result.request_id == "fal-request"


def test_google_chirp_speech_uses_chirp_voice_and_mp3():
    class Types:
        class AudioEncoding:
            MP3 = "MP3"

        class SynthesisInput:
            def __init__(self, **kwargs): self.kwargs = kwargs

        class VoiceSelectionParams:
            def __init__(self, **kwargs): self.kwargs = kwargs

        class AudioConfig:
            def __init__(self, **kwargs): self.kwargs = kwargs

    class Client:
        def synthesize_speech(self, **kwargs):
            self.kwargs = kwargs
            return type("Response", (), {"audio_content": b"mp3"})()

    client = Client()
    provider = GoogleChirpSpeechProvider(client=client, texttospeech_module=Types)
    result = provider.synthesize(SpeechSynthesisRequest(text="Hello.", speed=1.25))
    assert result.audio_bytes == b"mp3"
    assert client.kwargs["voice"].kwargs["name"] == "en-US-Chirp3-HD-Charon"
    assert client.kwargs["audio_config"].kwargs["speaking_rate"] == 1.25


def test_fal_seed_speech_voice_selection_cues_and_exclusion():
    from providers.fal_seed_speech_provider import FEMALE_SEED_VOICES, MALE_SEED_VOICES

    provider = FalSeedSpeechProvider(api_key="test")
    # Female voice tag
    female_voice = provider.select_voice(["female"])
    assert female_voice in FEMALE_SEED_VOICES

    # Dict character metadata with voice_tags
    male_voice = provider.select_voice({
        "name": "Cedric",
        "description": "Looking for his sister and mother",
        "voice_tags": ["male"],
    })
    assert male_voice in MALE_SEED_VOICES

    # Stable selection for same voice tags
    assert provider.select_voice(["female"]) == provider.select_voice(["female"])

    # Exclude already used voice
    excluded_voice = provider.select_voice(["female"])
    next_voice = provider.select_voice(["female"], exclude=[excluded_voice])
    assert next_voice != excluded_voice
    assert next_voice in FEMALE_SEED_VOICES


def test_gemini_speech_voice_selection():
    from providers.gemini_speech_provider import GEMINI_FEMALE_VOICES, GEMINI_MALE_VOICES

    provider = GeminiSpeechProvider()
    female_voice = provider.select_voice(["female"])
    assert female_voice in GEMINI_FEMALE_VOICES

    male_voice = provider.select_voice(["male"])
    assert male_voice in GEMINI_MALE_VOICES


def test_google_chirp_speech_voice_selection():
    from providers.google_chirp_speech_provider import CHIRP_FEMALE_VOICES, CHIRP_MALE_VOICES

    provider = GoogleChirpSpeechProvider()
    female_voice = provider.select_voice(["female"])
    assert female_voice in CHIRP_FEMALE_VOICES

    male_voice = provider.select_voice(["male"])
    assert male_voice in CHIRP_MALE_VOICES


def test_speech_provider_synthesize_uses_selected_voice():
    calls = []

    def post(endpoint, payload):
        calls.append((endpoint, payload))
        return {"request_id": "fal-req", "audio": {"url": "https://audio.example/out.mp3"}}

    provider = FalSeedSpeechProvider(api_key="test", request_json=post, download=lambda _: (b"mp3", "audio/mpeg"))
    chosen_voice = provider.select_voice(["female"])
    result = provider.synthesize(SpeechSynthesisRequest(text="Cast the spell.", voice=chosen_voice))

    assert calls[0][1]["voice"] == chosen_voice
    assert result.audio_bytes == b"mp3"

