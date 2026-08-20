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


def test_dispatch_newer_scene_aborts_previous_scene_synthesis(tmp_path: Path):
    import threading

    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.return_value = "voice_alpha"

    published = []
    dispatcher = SceneSpeechDispatcher(
        theater_id="stage-1",
        output_dir=tmp_path,
        assignments={},
        persist_assignments=lambda: None,
        publish_audio=published.append,
        provider=mock_provider,
    )

    scene1_started = threading.Event()
    scene2_dispatched = threading.Event()

    def slow_synthesize(req):
        if req.text == "Scene 1 line 1":
            scene1_started.set()
            scene2_dispatched.wait(timeout=2.0)
        return SpeechSynthesisResult(
            audio_bytes=b"audio",
            mime_type="audio/mpeg",
            provider="mock",
            model="mock",
        )

    mock_provider.synthesize.side_effect = slow_synthesize

    dispatcher.dispatch([
        {"speaker": "Alice", "text": "Scene 1 line 1", "kind": "speech"},
        {"speaker": "Alice", "text": "Scene 1 line 2", "kind": "speech"},
    ])
    assert scene1_started.wait(timeout=2.0)
    dispatcher.dispatch([{"speaker": "Bob", "text": "Scene 2 line 1", "kind": "speech"}])
    scene2_dispatched.set()

    dispatcher._executor.shutdown(wait=True)

    # Scene 1 line 1 was discarded after synthesis, line 2 was aborted before synthesis.
    # Only Scene 2 line 1 was published.
    assert len(published) == 1
    assert published[0]["speaker"] == "Bob"


def test_cancel_aborts_in_flight_synthesis(tmp_path: Path):
    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.return_value = "voice_alpha"

    published = []
    dispatcher = SceneSpeechDispatcher(
        theater_id="stage-1",
        output_dir=tmp_path,
        assignments={},
        persist_assignments=lambda: None,
        publish_audio=published.append,
        provider=mock_provider,
    )

    def cancelling_synthesize(req):
        dispatcher.cancel()
        return SpeechSynthesisResult(
            audio_bytes=b"audio",
            mime_type="audio/mpeg",
            provider="mock",
            model="mock",
        )

    mock_provider.synthesize.side_effect = cancelling_synthesize

    dispatcher.dispatch([{"speaker": "Alice", "text": "Scene 1", "kind": "speech"}])
    dispatcher._executor.shutdown(wait=True)

    assert len(published) == 0


