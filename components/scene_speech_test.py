from pathlib import Path
from unittest.mock import MagicMock

from components.scene_speech import SceneSpeechDispatcher, speaker_key
from providers.speech_provider import SpeechProvider, SpeechSynthesisResult


def test_speaker_key_normalizes_equivalent_display_names():
    assert speaker_key("  Mara   Venn ") == "mara venn"


def test_voice_assignment_is_stable_and_uses_an_unused_preset(tmp_path: Path):
    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.side_effect = lambda tags, exclude=(): (
        "preset_voice_2" if "preset_voice_1" in (exclude or ()) else "preset_voice_1"
    )

    assignments = {"existing": "preset_voice_1"}
    persisted = []
    dispatcher = SceneSpeechDispatcher(
        theater_id="lantern-house",
        output_dir=tmp_path,
        assignments=assignments,
        persist_assignments=lambda: persisted.append(True),
        publish_audio=lambda _message: None,
        provider=mock_provider,
    )

    voice = dispatcher._voice_for("Mara Venn")

    assert voice == "preset_voice_2"
    assert assignments["mara venn"] == "preset_voice_2"
    assert dispatcher._voice_for("  mara   venn ") == "preset_voice_2"
    assert persisted == [True]


def test_dispatcher_delegates_to_provider_select_voice(tmp_path: Path):
    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.return_value = "custom_voice_alpha"

    assignments = {}
    dispatcher = SceneSpeechDispatcher(
        theater_id="stage-1",
        output_dir=tmp_path,
        assignments=assignments,
        persist_assignments=lambda: None,
        publish_audio=lambda _: None,
        character_lookup=lambda name: ["male"],
        provider=mock_provider,
    )

    voice = dispatcher._voice_for("Arthur")
    assert voice == "custom_voice_alpha"
    assert assignments["arthur"] == "custom_voice_alpha"
    mock_provider.select_voice.assert_called_once_with(["male"], exclude=set())

    # Second call uses canvas state assignment without re-querying provider
    mock_provider.select_voice.reset_mock()
    voice2 = dispatcher._voice_for("Arthur")
    assert voice2 == "custom_voice_alpha"
    mock_provider.select_voice.assert_not_called()


def test_dispatch_synthesizes_and_publishes_audio(tmp_path: Path):
    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.return_value = "voice_alpha"
    mock_provider.synthesize.return_value = SpeechSynthesisResult(
        audio_bytes=b"fake_mp3_audio",
        mime_type="audio/mpeg",
        provider="mock-provider",
        model="mock-model",
    )

    published = []
    dispatcher = SceneSpeechDispatcher(
        theater_id="stage-1",
        output_dir=tmp_path,
        assignments={},
        persist_assignments=lambda: None,
        publish_audio=published.append,
        provider=mock_provider,
    )

    dispatcher.dispatch([
        {"speaker": "Mara", "text": "Hello world", "kind": "speech"},
        {"speaker": "Mara", "text": "I think silently", "kind": "thought"},
    ])
    dispatcher._executor.shutdown(wait=True)

    mock_provider.synthesize.assert_called_once()
    assert len(published) == 1
    assert published[0]["speaker"] == "Mara"
    assert published[0]["voice"] == "voice_alpha"
    assert published[0]["audio_url"].startswith("/theaters/stage-1/output/speech/")

