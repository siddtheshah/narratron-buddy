import threading
from unittest.mock import MagicMock, Mock

from components.canvas.story_state import StoryState, speaker_key
from providers.speech_provider import SpeechProvider, SpeechSynthesisResult


def test_story_state_isolated_from_other_theaters() -> None:
    first, second = StoryState(), StoryState()
    first.named_elements.append({"name": "Ada"})
    assert second.named_elements == []


def test_speaker_key_normalizes_equivalent_display_names() -> None:
    assert speaker_key("  Mara   Venn ") == "mara venn"
    assert speaker_key("") == "narrator"


def test_dialogue_persists_beautified_lines_and_notifies() -> None:
    persist, notify = Mock(), Mock()
    beautifier = Mock()
    beautifier.beautify_text.return_value = [{"text": "Hello", "effect": "vibrate"}]

    state = StoryState(persist=persist, notify_changed=notify)
    state.text_beautifier = beautifier

    state.set_scene_dialogue([{"speaker": "Mara", "text": "Hello"}])

    assert state.scene_dialogue == [{"speaker": "Mara", "text": "Hello", "spans": [{"text": "Hello", "effect": "vibrate"}]}]
    persist.assert_called_once_with()
    notify.assert_called_once_with("latest")


def test_narration_uses_supplied_spans_without_beautifying() -> None:
    persist, notify = Mock(), Mock()
    beautifier = Mock()

    state = StoryState(persist=persist, notify_changed=notify)
    state.text_beautifier = beautifier

    state.set_narration("The ground trembles!", spans=[{"text": "TREMBLES", "effect": "vibrate"}])

    assert state.narration == "The ground trembles!"
    assert state.narration_spans == [{"text": "TREMBLES", "effect": "vibrate"}]
    beautifier.beautify_text.assert_not_called()
    persist.assert_called_once_with()
    notify.assert_called_once_with("latest")


def test_character_voice_assignment_is_normalized_and_serialized() -> None:
    persist = Mock()
    state = StoryState(persist=persist)

    state.assign_character_voice(" Mara   Venn ", "dacey_en")

    assert state.get_character_voice("mara venn") == "dacey_en"
    assert state.serialize()["character_voice_assignments"] == {"mara venn": "dacey_en"}
    persist.assert_called_once_with()


def test_character_voice_tags_and_description_from_planning_state() -> None:
    state = StoryState()
    state.load({
        "story_planning_state": {
            "characters": [
                {
                    "name": "Mara",
                    "voice_tags": ["female"],
                    "description": "A clever inventor",
                    "personality": "Curious",
                }
            ],
            "sticky_notes": [
                {"name": "Old Tower", "content": "An ancient crumbling tower on the hill"}
            ],
        }
    })

    assert state.get_character_voice_tags("Mara") == ["female"]
    assert "clever inventor" in state.get_character_description("Mara")
    assert "ancient crumbling tower" in state.get_character_description("Old Tower")
    assert state.get_character_description("Unknown Stranger") == "Unknown Stranger"


def test_voice_assignment_is_stable_and_uses_an_unused_preset() -> None:
    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.side_effect = lambda tags, exclude=(): (
        "preset_voice_2" if "preset_voice_1" in (exclude or ()) else "preset_voice_1"
    )

    persisted = []
    state = StoryState(persist=lambda: persisted.append(True))
    state.character_voice_assignments = {"existing": "preset_voice_1"}
    state.enable_scene_speech(mock_provider)

    voice = state._voice_for("Mara Venn")

    assert voice == "preset_voice_2"
    assert state.character_voice_assignments["mara venn"] == "preset_voice_2"
    assert state._voice_for("  mara   venn ") == "preset_voice_2"
    assert persisted == [True]


def test_story_state_delegates_to_provider_select_voice() -> None:
    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.return_value = "custom_voice_alpha"

    state = StoryState()
    state.enable_scene_speech(mock_provider)
    state._character_lookup = lambda name: ["male"]

    voice = state._voice_for("Arthur")
    assert voice == "custom_voice_alpha"
    assert state.character_voice_assignments["arthur"] == "custom_voice_alpha"
    mock_provider.select_voice.assert_called_once_with(["male"], exclude=set())

    # Second call uses assigned voice without re-querying provider
    mock_provider.select_voice.reset_mock()
    voice2 = state._voice_for("Arthur")
    assert voice2 == "custom_voice_alpha"
    mock_provider.select_voice.assert_not_called()


def test_dispatch_synthesizes_and_publishes_audio_as_data_uri() -> None:
    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.return_value = "voice_alpha"
    mock_provider.synthesize.return_value = SpeechSynthesisResult(
        audio_bytes=b"fake_mp3_audio",
        mime_type="audio/mpeg",
        provider="mock-provider",
        model="mock-model",
    )

    published = []
    state = StoryState(publish_audio_fn=published.append)
    state.enable_scene_speech(mock_provider)

    state.dispatch([
        {"speaker": "Mara", "text": "Hello world", "kind": "speech"},
        {"speaker": "Mara", "text": "I think silently", "kind": "thought"},
    ])
    state._executor.shutdown(wait=True)

    mock_provider.synthesize.assert_called_once()
    assert len(published) == 1
    assert published[0]["type"] == "scene_speech_ready"
    assert published[0]["speaker"] == "Mara"
    assert published[0]["voice"] == "voice_alpha"
    assert published[0]["audio_url"].startswith("data:audio/mpeg;base64,")


def test_dispatch_newer_scene_aborts_previous_scene_synthesis() -> None:
    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.return_value = "voice_alpha"

    published = []
    state = StoryState(publish_audio_fn=published.append)
    state.enable_scene_speech(mock_provider)

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

    state.dispatch([
        {"speaker": "Alice", "text": "Scene 1 line 1", "kind": "speech"},
        {"speaker": "Alice", "text": "Scene 1 line 2", "kind": "speech"},
    ])
    assert scene1_started.wait(timeout=2.0)
    state.dispatch([{"speaker": "Bob", "text": "Scene 2 line 1", "kind": "speech"}])
    scene2_dispatched.set()

    state._executor.shutdown(wait=True)

    # Scene 1 was aborted, only Scene 2 was published
    assert len(published) == 1
    assert published[0]["speaker"] == "Bob"


def test_cancel_aborts_in_flight_synthesis() -> None:
    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.return_value = "voice_alpha"

    published = []
    state = StoryState(publish_audio_fn=published.append)
    state.enable_scene_speech(mock_provider)

    def cancelling_synthesize(req):
        state.cancel()
        return SpeechSynthesisResult(
            audio_bytes=b"audio",
            mime_type="audio/mpeg",
            provider="mock",
            model="mock",
        )

    mock_provider.synthesize.side_effect = cancelling_synthesize

    state.dispatch([{"speaker": "Alice", "text": "Scene 1", "kind": "speech"}])
    state._executor.shutdown(wait=True)

    assert len(published) == 0


def test_story_state_publish_audio_fn_delegates_to_connection_state_broadcast() -> None:
    from components.canvas.connection_state import ConnectionState
    conn = ConnectionState()
    received = []
    conn.broadcast = received.append

    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.return_value = "voice_alpha"
    mock_provider.synthesize.return_value = SpeechSynthesisResult(
        audio_bytes=b"audio",
        mime_type="audio/mpeg",
        provider="mock",
        model="mock",
    )

    state = StoryState(publish_audio_fn=conn.broadcast)
    state.enable_scene_speech(mock_provider)

    state.dispatch([{"speaker": "Alice", "text": "Speaking aloud", "kind": "speech"}])
    state._executor.shutdown(wait=True)

    assert len(received) == 1
    assert received[0]["type"] == "scene_speech_ready"
    assert received[0]["speaker"] == "Alice"



def test_load_and_serialize_round_trip() -> None:
    state = StoryState()
    data = {
        "named_elements": [{"name": "Sword", "type": "item"}],
        "story_planning_state": {"arc": "climax"},
        "scene_dialogue": [{"speaker": "Hero", "text": "Victory!"}],
        "narration": "The sun rises over the citadel.",
        "narration_spans": [{"text": "rises", "effect": "glow"}],
        "character_voice_assignments": {"hero": "voice_1"},
    }

    state.load(data)

    serialized = state.serialize()
    assert serialized["named_elements"] == [{"name": "Sword", "type": "item"}]
    assert serialized["story_planning_state"] == {"arc": "climax"}
    assert serialized["scene_dialogue"] == [{"speaker": "Hero", "text": "Victory!"}]
    assert serialized["narration"] == "The sun rises over the citadel."
    assert serialized["narration_spans"] == [{"text": "rises", "effect": "glow"}]
    assert serialized["character_voice_assignments"] == {"hero": "voice_1"}
    assert state.payload() == serialized


def test_set_narration_truncates_at_45_words_and_500_chars() -> None:
    state = StoryState()
    state.text_beautifier = False
    long_text = " ".join([f"word{i}" for i in range(60)])
    state.set_narration(long_text)

    words = state.narration.split()
    assert len(words) == 45
    assert len(state.narration) <= 500


def test_set_scene_dialogue_caps_at_three_entries() -> None:
    state = StoryState()
    state.text_beautifier = False
    five_lines = [{"speaker": f"Char_{i}", "text": f"Line {i}"} for i in range(5)]
    state.set_scene_dialogue(five_lines)

    assert len(state.scene_dialogue) == 3
    assert state.scene_dialogue[0]["speaker"] == "Char_0"
    assert state.scene_dialogue[2]["speaker"] == "Char_2"


def test_sticky_notes_falls_back_to_named_elements() -> None:
    state = StoryState()
    state.named_elements = [{"name": "Relic", "type": "artifact"}]
    # No sticky_notes key in story_planning_state
    assert state.sticky_notes() == [{"name": "Relic", "type": "artifact"}]

    # When explicit sticky_notes present in planning state
    state.story_planning_state["sticky_notes"] = [{"name": "Note 1", "info": "Clue"}]
    assert state.sticky_notes() == [{"name": "Note 1", "info": "Clue"}]


def test_get_character_voice_tags_filters_to_binary_gender_and_handles_single_string() -> None:
    state = StoryState()
    state.story_planning_state = {
        "characters": [
            {"name": "Elder", "voice_tags": ["elderly", "deep", "male", "wise"]},
            {"name": "Princess", "voice_tags": "female"},
            {"name": "Robot", "voice_tags": ["robotic", "metallic"]},
        ]
    }

    # Only "male" is retained
    assert state.get_character_voice_tags("Elder") == ["male"]
    # Single string "female" handled
    assert state.get_character_voice_tags("Princess") == ["female"]
    # Non-gender tags filtered to empty list
    assert state.get_character_voice_tags("Robot") == []


def test_load_handles_none_or_non_dict_gracefully() -> None:
    state = StoryState()
    state.narration = "Original"

    state.load(None)
    assert state.narration == "Original"

    state.load("not_a_dict")
    assert state.narration == "Original"

