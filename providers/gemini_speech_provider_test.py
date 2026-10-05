"""Regression coverage for stable, tag-aware Gemini casting."""

import base64
from unittest.mock import MagicMock

import pytest
from google import genai
from google.genai.voices import Voice, VoiceListResponse

from providers.gemini_speech_provider import (
    GEMINI_FEMALE_VOICES,
    GEMINI_MALE_VOICES,
    CatalogPage,
    CatalogVoice,
    GeminiSpeechProvider,
    VoiceProfile,
)
from providers.speech_provider import SpeechSynthesisRequest


def _client(voices: list[CatalogVoice]) -> MagicMock:
    client = MagicMock(spec=genai.Client)
    client.voices.list.return_value = CatalogPage(voices=voices)
    return client


@pytest.mark.parametrize("profile", [
    ["gender=female", "accent=British"],
    [" ACCENT:british ", "female", "gender=FEMALE"],
    {"gender": "female", "accent": "BRITISH"},
    {"voice_tags": ["female", "accent=British"], "description": "Changed prose"},
])
def test_equivalent_profiles_and_catalog_orders_choose_same_voice(profile: VoiceProfile) -> None:
    voices = [CatalogVoice(id=f"voice_{index}") for index in range(12)]
    expected = GeminiSpeechProvider(client=_client(voices)).select_voice(["female", "accent=British"])
    actual = GeminiSpeechProvider(client=_client(list(reversed(voices)))).select_voice(profile)
    assert actual == expected


def test_character_description_edits_do_not_change_casting() -> None:
    voices = [CatalogVoice(id=f"voice_{index}") for index in range(12)]
    first = GeminiSpeechProvider(client=_client(voices)).select_voice({
        "name": "Mara", "voice_tags": ["female"], "description": "A young scout",
    })
    second = GeminiSpeechProvider(client=_client(voices)).select_voice({
        "name": "Mara", "voice_tags": ["female"], "description": "A seasoned commander",
    })
    assert first == second


def test_excluding_an_unused_voice_does_not_change_the_winner() -> None:
    voices = [CatalogVoice(id=f"voice_{index}") for index in range(12)]
    provider = GeminiSpeechProvider(client=_client(voices))
    first = provider.select_voice(["male"])
    unused = next(voice.id for voice in voices if voice.id and voice.id != first)
    assert provider.select_voice(["male"], exclude=[unused]) == first
    assert provider.select_voice(["male"], exclude=[first]) != first


def test_selection_reads_all_pages_with_identical_filters() -> None:
    client = _client([])
    client.voices.list.side_effect = [
        VoiceListResponse(voices=[Voice(type="prebuilt", id="used")], next_page_token="page_two"),
        VoiceListResponse(voices=[Voice(type="prebuilt", id="available")]),
    ]
    provider = GeminiSpeechProvider(client=client)
    assert provider.select_voice(["male"], exclude=["used"]) == "available"
    assert client.voices.list.call_args_list[0].kwargs == {
        "type_": ["prebuilt"], "page_size": 1000, "gender": ["male"], "language_code": ["en-US"],
    }
    assert client.voices.list.call_args_list[1].kwargs == {
        **client.voices.list.call_args_list[0].kwargs, "page_token": "page_two",
    }


def test_discovery_uses_sdk_context_field_and_all_pages() -> None:
    client = _client([])
    client.voices.list.side_effect = [
        VoiceListResponse(voices=[Voice(type="prebuilt", context="Audiobook", gender="female")], next_page_token="next"),
        VoiceListResponse(voices=[Voice(type="prebuilt", context="News", gender="neutral")]),
    ]
    provider = GeminiSpeechProvider(client=client)
    assert provider.get_supported_voice_tags() == {
        "gender": ("female", "nonbinary"), "contexts": ("Audiobook", "News"),
    }
    provider.get_supported_voice_tags()
    assert client.voices.list.call_count == 2


def test_repeated_tags_and_direct_gender_are_used_as_filters() -> None:
    client = _client([CatalogVoice(id="matching")])
    provider = GeminiSpeechProvider(client=client, voice_language_code=None)
    assert provider.select_voice({
        "gender": "male", "voice_tags": ["Pitch=low", "pitch:medium", "context:Audiobook"],
    }) == "matching"
    assert client.voices.list.call_args.kwargs == {
        "type_": ["prebuilt"], "page_size": 1000, "gender": ["male"],
        "pitch": ["low", "medium"], "contexts": ["Audiobook"],
    }


def test_empty_exact_match_ranks_partial_matches_without_relaxing_explicit_gender_or_language() -> None:
    client = _client([])
    client.voices.list.side_effect = [CatalogPage(voices=[]), CatalogPage(voices=[
        CatalogVoice(id="british_low", accent="British", pitch="low"),
        CatalogVoice(id="british_high", accent="British", pitch="high", persona="Narrator"),
        CatalogVoice(id="american_low", accent="American", pitch="low", persona="Narrator"),
    ])]
    provider = GeminiSpeechProvider(client=client)
    assert provider.select_voice(["male", "language_code=en-US", "accent=British", "pitch=low", "persona=Narrator"]) == "british_low"
    assert client.voices.list.call_args_list[1].kwargs == {
        "type_": ["prebuilt"], "page_size": 1000, "gender": ["male"], "language_code": ["en-US"],
    }


def test_default_us_locale_allows_english_regional_accents() -> None:
    client = _client([])
    client.voices.list.side_effect = [CatalogPage(voices=[]), CatalogPage(voices=[
        CatalogVoice(id="british", language_code="en-GB", accent="British", pitch="low"),
        CatalogVoice(id="american", language_code="en-US", accent="American", pitch="low"),
        CatalogVoice(id="french", language_code="fr-FR", accent="British", pitch="low", persona="Narrator"),
    ])]
    provider = GeminiSpeechProvider(client=client)
    assert provider.select_voice(["male", "accent=British", "pitch=low", "persona=Narrator"]) == "british"
    assert client.voices.list.call_args_list[1].kwargs == {
        "type_": ["prebuilt"], "page_size": 1000, "gender": ["male"],
    }


def test_untyped_traits_rank_catalog_descriptions() -> None:
    provider = GeminiSpeechProvider(client=_client([
        CatalogVoice(id="cold", description="A sharp, commanding voice"),
        CatalogVoice(id="warm", description="A warm, friendly voice"),
    ]))
    assert provider.select_voice(["female", "warm", "friendly"]) == "warm"


@pytest.mark.parametrize(("tags", "expected"), [
    (["female", "warm"], "Sulafat"),
    (["male", "gravelly"], "Algenib"),
    (["female", "persona=Firm"], "Kore"),
])
def test_offline_fallback_uses_documented_voice_traits(tags: list[str], expected: str) -> None:
    client = _client([])
    client.voices.list.side_effect = RuntimeError("offline")
    provider = GeminiSpeechProvider(client=client)
    assert provider.select_voice(tags) == expected


def test_exhausted_gender_pool_reuses_matching_voice() -> None:
    provider = GeminiSpeechProvider(client=_client([]))
    assert "Zephyr" in GEMINI_FEMALE_VOICES
    assert "Zephyr" not in GEMINI_MALE_VOICES
    assert provider.select_voice(["female"], exclude=GEMINI_FEMALE_VOICES) in GEMINI_FEMALE_VOICES
    assert provider.select_voice(["male"], exclude=GEMINI_MALE_VOICES) in GEMINI_MALE_VOICES


def test_network_recovery_does_not_recast_existing_profile() -> None:
    client = _client([])
    client.voices.list.side_effect = RuntimeError("offline")
    provider = GeminiSpeechProvider(client=client)
    first = provider.select_voice(["male"])
    client.voices.list.side_effect = None
    client.voices.list.return_value = CatalogPage(voices=[CatalogVoice(id="new_voice")])
    assert provider.select_voice(["male"]) == first
    assert client.voices.list.call_count == 1


def test_synthesis_selects_from_tags_and_preserves_explicit_voice() -> None:
    client = _client([CatalogVoice(id="tagged_voice")])
    client.interactions.create.return_value = {"output_audio": {"data": base64.b64encode(b"audio").decode("ascii")}}
    provider = GeminiSpeechProvider(client=client)
    provider.synthesize(SpeechSynthesisRequest(text="Hello", voice_tags=("male",)))
    assert client.interactions.create.call_args.kwargs["generation_config"]["speech_config"] == [{"voice": "tagged_voice"}]
    provider.synthesize(SpeechSynthesisRequest(text="Hello", voice="Charon", voice_tags=("male",)))
    assert client.interactions.create.call_args.kwargs["generation_config"]["speech_config"] == [{"voice": "Charon"}]
