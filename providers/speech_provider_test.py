import base64
import wave
from io import BytesIO

import pytest

from providers.fal_seed_speech_provider import FalSeedSpeechProvider
from providers.gemini_speech_provider import GeminiSpeechProvider
from providers.google_chirp_speech_provider import GoogleChirpSpeechProvider
from providers.speech_provider import SpeechProviderError, SpeechSynthesisRequest


def test_speech_request_validation():
    with pytest.raises(ValueError, match="cannot be empty"):
        SpeechSynthesisRequest(text=" ")
    with pytest.raises(ValueError, match="speed"):
        SpeechSynthesisRequest(text="Hello", speed=0)


def _wav_b64(frames: bytes = b"\x01\x00\x02\x00") -> str:
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24_000)
        wav.writeframes(frames)
    return base64.b64encode(output.getvalue()).decode("ascii")


def test_gemini_speech_uses_structured_style_metadata_and_returns_wav():
    class Interactions:
        @staticmethod
        def create(**kwargs):
            assert kwargs["generation_config"]["speech_config"][0]["voice"] == "Kore"
            assert kwargs["input"] == [{
                "type": "user_input",
                "content": [{
                    "type": "text",
                    "text": "Hello.",
                    "annotations": [{"type": "speech_metadata", "style": "Speak warmly."}],
                }],
            }]
            assert kwargs["response_format"] == {"type": "audio", "mime_type": "audio/wav"}
            return {"output_audio": {"data": _wav_b64()}, "request_id": "gemini-request"}

    provider = GeminiSpeechProvider(client=type("Client", (), {"interactions": Interactions()})())
    result = provider.synthesize(SpeechSynthesisRequest(text="Hello.", voice_instruction="Speak warmly."))
    assert result.mime_type == "audio/wav"
    assert result.request_id == "gemini-request"
    with wave.open(BytesIO(result.audio_bytes)) as output:
        assert output.getframerate() == 24_000
        assert output.readframes(2) == b"\x01\x00\x02\x00"


def test_gemini_speech_uses_verbatim_transcript_and_retries_empty_audio():
    calls = []

    class Interactions:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {"output_audio": None}
            return {"output_audio": {"data": _wav_b64()}}

    provider = GeminiSpeechProvider(
        client=type("Client", (), {"interactions": Interactions()})(),
        retry_delay_seconds=0,
    )
    result = provider.synthesize(SpeechSynthesisRequest(text="Hello there."))

    assert len(calls) == 2
    assert calls[0]["input"] == [{
        "type": "user_input",
        "content": [{"type": "text", "text": "Hello there."}],
    }]
    assert result.audio_bytes.startswith(b"RIFF")


def test_gemini_speech_reports_failure_after_retry_limit():
    class Interactions:
        @staticmethod
        def create(**kwargs):
            raise RuntimeError("temporary server error")

    provider = GeminiSpeechProvider(
        client=type("Client", (), {"interactions": Interactions()})(),
        max_attempts=2,
        retry_delay_seconds=0,
    )

    with pytest.raises(SpeechProviderError, match=r"after 2 attempt\(s\).*temporary server error"):
        provider.synthesize(SpeechSynthesisRequest(text="Hello."))


def test_gemini_speech_does_not_retry_permanent_client_errors():
    calls = 0

    class PermanentClientError(RuntimeError):
        code = 400

    class Interactions:
        @staticmethod
        def create(**kwargs):
            nonlocal calls
            calls += 1
            raise PermanentClientError("invalid request")

    provider = GeminiSpeechProvider(
        client=type("Client", (), {"interactions": Interactions()})(),
        max_attempts=3,
        retry_delay_seconds=0,
    )

    with pytest.raises(SpeechProviderError, match=r"after 1 attempt\(s\).*invalid request"):
        provider.synthesize(SpeechSynthesisRequest(text="Hello."))
    assert calls == 1


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

    # Dict character metadata with gender field fallback
    male_from_gender = provider.select_voice({
        "name": "Cedric",
        "gender": "male",
    })
    assert male_from_gender in MALE_SEED_VOICES

    # Nonbinary voice tag
    from providers.fal_seed_speech_provider import SEED_CHARACTER_VOICES
    nb_voice = provider.select_voice(["nonbinary"])
    assert nb_voice in SEED_CHARACTER_VOICES

    # Stable selection for same voice tags
    assert provider.select_voice(["female"]) == provider.select_voice(["female"])

    # Exclude already used voice
    excluded_voice = provider.select_voice(["female"])
    next_voice = provider.select_voice(["female"], exclude=[excluded_voice])
    assert next_voice != excluded_voice
    assert next_voice in FEMALE_SEED_VOICES


def test_gemini_speech_voice_selection():
    from providers.gemini_speech_provider import (
        GEMINI_FEMALE_VOICES,
        GEMINI_MALE_VOICES,
        GEMINI_VOICES,
    )

    provider = GeminiSpeechProvider(client=object())
    female_voice = provider.select_voice(["female"])
    assert female_voice in GEMINI_FEMALE_VOICES

    male_voice = provider.select_voice(["male"])
    assert male_voice in GEMINI_MALE_VOICES

    nb_voice = provider.select_voice(["nonbinary"])
    assert nb_voice in GEMINI_VOICES

    assert len(provider.select_voice(exclude=GEMINI_VOICES[:-1])) > 0


def test_gemini_speech_selects_and_caches_extended_library_voices():
    calls = []

    class Voices:
        @staticmethod
        def list(**kwargs):
            calls.append(kwargs)
            return {"voices": [{"id": "voice_british_scout"}, {"id": "voice_british_knight"}]}

    provider = GeminiSpeechProvider(client=type("Client", (), {"voices": Voices()})())
    profile = {
        "voice_tags": ["female"],
        "description": "A clever scout from the western marches.",
        "language_code": "en-GB",
        "accent": "British",
        "persona": "Narrator",
    }

    first = provider.select_voice(profile)
    second = provider.select_voice(profile)

    assert first in {"voice_british_scout", "voice_british_knight"}
    assert second == first
    assert calls == [{
        "type_": ["prebuilt"],
        "page_size": 1000,
        "gender": ["female"],
        "language_code": ["en-GB"],
        "accent": ["British"],
        "persona": ["Narrator"],
    }]


def test_gemini_speech_lists_live_voice_tag_values_and_caches_them():
    calls = []

    class Voices:
        @staticmethod
        def list(**kwargs):
            calls.append(kwargs)
            return {
                "voices": [
                    {"gender": "female", "accent": "British", "language_code": "en-GB"},
                    {"gender": "neutral", "accent": "General American", "persona": "Narrator"},
                ]
            }

    provider = GeminiSpeechProvider(client=type("Client", (), {"voices": Voices()})())

    assert provider.get_supported_voice_tags() == {
        "accent": ("British", "General American"),
        "gender": ("female", "nonbinary"),
        "language_code": ("en-GB",),
        "persona": ("Narrator",),
    }
    assert provider.get_supported_voice_tags()["accent"] == ("British", "General American")
    assert calls == [{"type_": ["prebuilt"], "page_size": 1000}]


def test_gemini_speech_resolves_typed_character_voice_tags_to_catalog_filters():
    calls = []

    class Voices:
        @staticmethod
        def list(**kwargs):
            calls.append(kwargs)
            return {"voices": [{"id": "voice_british_scout"}]}

    provider = GeminiSpeechProvider(client=type("Client", (), {"voices": Voices()})())
    assert provider.select_voice(["gender=female", "accent=British"]) == "voice_british_scout"
    assert calls == [{
        "type_": ["prebuilt"], "page_size": 1000,
        "gender": ["female"], "language_code": ["en-US"], "accent": ["British"],
    }]


def test_gemini_speech_extended_library_respects_excluded_voices():
    class Voices:
        @staticmethod
        def list(**kwargs):
            return {"voices": [{"id": "voice_one"}, {"id": "voice_two"}]}

    provider = GeminiSpeechProvider(client=type("Client", (), {"voices": Voices()})())
    assert provider.select_voice(["male"], exclude=["voice_one"]) == "voice_two"


def test_google_chirp_speech_voice_selection():
    from providers.google_chirp_speech_provider import (
        CHIRP_FEMALE_VOICES,
        CHIRP_MALE_VOICES,
        CHIRP_VOICES,
    )

    provider = GoogleChirpSpeechProvider()
    female_voice = provider.select_voice(["female"])
    assert female_voice in CHIRP_FEMALE_VOICES

    male_voice = provider.select_voice(["male"])
    assert male_voice in CHIRP_MALE_VOICES

    nb_voice = provider.select_voice(["nonbinary"])
    assert nb_voice in CHIRP_VOICES


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


def test_speech_synthesis_request_accent_augmentation():
    # Accent augmentation enabled by default
    req_default = SpeechSynthesisRequest(
        text="Welcome traveler.",
        voice_tags=("gender=female", "accent=British"),
    )
    assert req_default.accent_augmentation is True
    assert req_default.style_instruction() == "Speak with a clearly pronounced British accent throughout."

    # Appends to existing voice instruction
    req_with_instruction = SpeechSynthesisRequest(
        text="Welcome traveler.",
        voice_instruction="Whisper cautiously.",
        voice_tags=("accent=Scottish",),
        accent_augmentation=True,
    )
    assert req_with_instruction.style_instruction() == (
        "Whisper cautiously. Speak with a clearly pronounced Scottish accent throughout."
    )

    # Disabled via request
    req_disabled = SpeechSynthesisRequest(
        text="Welcome traveler.",
        voice_instruction="Whisper cautiously.",
        voice_tags=("accent=Scottish",),
        accent_augmentation=False,
    )
    assert req_disabled.style_instruction() == "Whisper cautiously."

    # No accent tags
    req_no_accent = SpeechSynthesisRequest(
        text="Welcome traveler.",
        voice_instruction="Whisper cautiously.",
        voice_tags=("gender=male", "pitch=low"),
        accent_augmentation=True,
    )
    assert req_no_accent.style_instruction() == "Whisper cautiously."

    # Deduplication of accents
    req_dedup = SpeechSynthesisRequest(
        text="Welcome traveler.",
        voice_tags=("accent=Irish", "accent=irish"),
        accent_augmentation=True,
    )
    assert req_dedup.style_instruction() == "Speak with a clearly pronounced Irish accent throughout."


def test_gemini_speech_honors_request_accent_augmentation():
    captured_kwargs = None

    class Interactions:
        @staticmethod
        def create(**kwargs):
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            return {"output_audio": {"data": _wav_b64()}, "request_id": "gemini-req"}

    provider = GeminiSpeechProvider(client=type("Client", (), {"interactions": Interactions()})())

    # Augmented
    provider.synthesize(SpeechSynthesisRequest(
        text="Hello.",
        voice_tags=("accent=British",),
        accent_augmentation=True,
    ))
    assert captured_kwargs["input"][0]["content"][0]["annotations"] == [
        {"type": "speech_metadata", "style": "Speak with a clearly pronounced British accent throughout."}
    ]

    # Non-augmented
    captured_kwargs = None
    provider.synthesize(SpeechSynthesisRequest(
        text="Hello.",
        voice_tags=("accent=British",),
        accent_augmentation=False,
    ))
    assert "annotations" not in captured_kwargs["input"][0]["content"][0]


def test_fal_seed_speech_honors_request_accent_augmentation():
    calls = []

    def post(endpoint, payload):
        calls.append((endpoint, payload))
        return {"request_id": "fal-req", "audio": {"url": "https://audio.example/out.mp3"}}

    provider = FalSeedSpeechProvider(api_key="test", request_json=post, download=lambda _: (b"mp3", "audio/mpeg"))

    # Augmented
    provider.synthesize(SpeechSynthesisRequest(
        text="Hello.",
        voice_tags=("accent=French",),
        accent_augmentation=True,
    ))
    assert calls[0][1]["voice_instruction"] == "Speak with a clearly pronounced French accent throughout."

    # Non-augmented
    calls.clear()
    provider.synthesize(SpeechSynthesisRequest(
        text="Hello.",
        voice_tags=("accent=French",),
        accent_augmentation=False,
    ))
    assert "voice_instruction" not in calls[0][1]

