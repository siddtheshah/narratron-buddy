import threading
from unittest.mock import MagicMock, Mock

from components.canvas.story_state import StoryState, speaker_key
from providers.speech_provider import SpeechProvider, SpeechProviderError, SpeechSynthesisResult


def test_story_state_isolated_from_other_theaters() -> None:
    first, second = StoryState(), StoryState()
    first.named_elements.append({"name": "Ada"})
    assert second.named_elements == []


def test_speaker_key_normalizes_equivalent_display_names() -> None:
    assert speaker_key("  Mara   Venn ") == "mara venn"
    assert speaker_key("") == "narrator"


def test_scene_waits_for_beautification_then_commits_once() -> None:
    persist, notify = Mock(), Mock()
    beautifier = Mock()
    state = StoryState(persist=persist, notify_changed=notify)
    state.narration = "Previous scene."
    state.scene_dialogue = [{"speaker": "Old", "text": "Previous dialogue."}]

    def beautify_scene(narration, dialogue):
        assert state.narration == "Previous scene."
        assert state.scene_dialogue == [{"speaker": "Old", "text": "Previous dialogue."}]
        assert persist.call_count == 0
        assert notify.call_count == 0
        return {
            "narration_spans": [{"text": narration, "effect": "glow"}],
            "dialogue": [{**dialogue[0], "spans": [{"text": dialogue[0]["text"], "effect": "vibrate"}]}],
        }

    beautifier.beautify_scene.side_effect = beautify_scene
    state.text_beautifier = beautifier

    state.set_scene("A new scene begins.", [{"speaker": "Mara", "text": "Look!"}])

    assert state.narration == "A new scene begins."
    assert state.narration_spans == [{"text": "A new scene begins.", "effect": "glow"}]
    assert state.scene_dialogue == [{"speaker": "Mara", "text": "Look!", "spans": [{"text": "Look!", "effect": "vibrate"}]}]
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
    assert published[0]["speech_generation"] == 1


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
        "committed_scene_speech_generation": 4,
    }

    state.load(data)

    serialized = state.serialize()
    assert serialized["named_elements"] == [{"name": "Sword", "type": "item"}]
    assert serialized["story_planning_state"] == {"arc": "climax"}
    assert serialized["scene_dialogue"] == [{"speaker": "Hero", "text": "Victory!"}]
    assert serialized["narration"] == "The sun rises over the citadel."
    assert serialized["narration_spans"] == [{"text": "rises", "effect": "glow"}]
    assert serialized["character_voice_assignments"] == {"hero": "voice_1"}
    assert serialized["committed_scene_speech_generation"] == 4
    assert state.payload() == serialized


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


def test_get_and_set_story_planning_state_persists_and_notifies() -> None:
    persist, notify = Mock(), Mock()
    state = StoryState(persist=persist, notify_changed=notify)

    planning_data = {
        "plot_beats": [{"plot_beat": "A door opens."}],
        "deep_plan": {
            "plot_beats": ["A second door opens."],
            "sticky_notes": [{"topic": "Key", "info": "Brass key"}],
        },
        "sticky_notes": [{"topic": "Key", "info": "Brass key"}],
    }
    state.set_story_planning_state(planning_data)

    expected = {
        "deep_plan": {
            "sticky_notes": [{"topic": "Key", "info": "Brass key"}],
        },
        "sticky_notes": [{"topic": "Key", "info": "Brass key"}],
    }
    assert state.get_story_planning_state() == expected
    assert state.serialize()["story_planning_state"] == expected
    assert state.sticky_notes() == [{"topic": "Key", "info": "Brass key"}]
    assert state.named_elements == [{"topic": "Key", "info": "Brass key"}]
    persist.assert_called_once_with()
    notify.assert_called_once_with("latest")


def test_get_and_set_sticky_notes_persists_and_notifies() -> None:
    persist, notify = Mock(), Mock()
    state = StoryState(persist=persist, notify_changed=notify)

    notes = [{"topic": "Chest", "info": "Heavy lock"}]
    state.set_sticky_notes(notes)

    assert state.get_sticky_notes() == notes
    assert state.story_planning_state["sticky_notes"] == notes
    assert state.named_elements == notes
    persist.assert_called_once_with()
    notify.assert_called_once_with("latest")


def test_dispatch_parallelizes_dialogue_line_synthesis() -> None:
    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.return_value = "voice_alpha"

    line1_started = threading.Event()
    line2_started = threading.Event()

    def concurrent_synthesize(req):
        if req.text == "Line 1":
            line1_started.set()
            assert line2_started.wait(timeout=2.0), "Line 2 did not start concurrently with Line 1"
        elif req.text == "Line 2":
            line2_started.set()
            assert line1_started.wait(timeout=2.0), "Line 1 did not start concurrently with Line 2"
        return SpeechSynthesisResult(
            audio_bytes=b"audio",
            mime_type="audio/mpeg",
            provider="mock",
            model="mock",
        )

    mock_provider.synthesize.side_effect = concurrent_synthesize

    published = []
    state = StoryState(publish_audio_fn=published.append)
    state.enable_scene_speech(mock_provider)

    state.dispatch([
        {"speaker": "Alice", "text": "Line 1", "kind": "speech"},
        {"speaker": "Bob", "text": "Line 2", "kind": "speech"},
    ])
    state._executor.shutdown(wait=True)

    assert len(published) == 2
    assert [p["speaker"] for p in published] == ["Alice", "Bob"]


def test_dispatch_preserves_dialogue_order_when_lines_complete_out_of_order() -> None:
    import time

    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.return_value = "voice_alpha"

    def out_of_order_synthesize(req):
        if req.text == "First line":
            time.sleep(0.08)
        elif req.text == "Second line":
            time.sleep(0.01)
        return SpeechSynthesisResult(
            audio_bytes=f"audio_{req.text}".encode(),
            mime_type="audio/mpeg",
            provider="mock",
            model="mock",
        )

    mock_provider.synthesize.side_effect = out_of_order_synthesize

    published = []
    state = StoryState(publish_audio_fn=published.append)
    state.enable_scene_speech(mock_provider)

    state.dispatch([
        {"speaker": "Alice", "text": "First line", "kind": "speech"},
        {"speaker": "Bob", "text": "Second line", "kind": "speech"},
    ])
    state._executor.shutdown(wait=True)

    assert len(published) == 2
    assert [p["speaker"] for p in published] == ["Alice", "Bob"]


def test_dispatch_handles_individual_line_synthesis_failure_gracefully() -> None:
    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.return_value = "voice_alpha"

    def fail_one_line(req):
        if req.text == "Failing line":
            raise SpeechProviderError("Provider unavailable")
        return SpeechSynthesisResult(
            audio_bytes=b"ok_audio",
            mime_type="audio/mpeg",
            provider="mock",
            model="mock",
        )

    mock_provider.synthesize.side_effect = fail_one_line

    published = []
    state = StoryState(publish_audio_fn=published.append)
    state.enable_scene_speech(mock_provider)

    state.dispatch([
        {"speaker": "Alice", "text": "Failing line", "kind": "speech"},
        {"speaker": "Bob", "text": "Succeeding line", "kind": "speech"},
    ])
    state._executor.shutdown(wait=True)

    assert len(published) == 1
    assert published[0]["speaker"] == "Bob"


def test_dispatch_cancel_aborts_multi_line_synthesis() -> None:
    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.return_value = "voice_alpha"

    synthesis_started = threading.Event()

    def cancelling_synthesize(req):
        synthesis_started.set()
        state.cancel()
        return SpeechSynthesisResult(
            audio_bytes=b"audio",
            mime_type="audio/mpeg",
            provider="mock",
            model="mock",
        )

    mock_provider.synthesize.side_effect = cancelling_synthesize

    published = []
    state = StoryState(publish_audio_fn=published.append)
    state.enable_scene_speech(mock_provider)

    state.dispatch([
        {"speaker": "Alice", "text": "Line 1", "kind": "speech"},
        {"speaker": "Bob", "text": "Line 2", "kind": "speech"},
    ])
    state._executor.shutdown(wait=True)

    assert len(published) == 0


def test_set_scene_parallelizes_text_beautification_with_speech_generation() -> None:
    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.return_value = "voice_alpha"

    speech_started = threading.Event()
    beautify_started = threading.Event()

    def concurrent_synthesize(req):
        speech_started.set()
        assert beautify_started.wait(timeout=2.0), "Beautification did not start"
        return SpeechSynthesisResult(
            audio_bytes=b"synth_audio",
            mime_type="audio/mpeg",
            provider="mock",
            model="mock",
        )

    mock_provider.synthesize.side_effect = concurrent_synthesize

    beautifier = Mock()

    def concurrent_beautify(narration, dialogue):
        beautify_started.set()
        assert speech_started.wait(timeout=2.0), "Speech synthesis was not dispatched concurrently with beautification"
        return {
            "narration_spans": [{"text": narration, "effect": "glow"}],
            "dialogue": [{**line, "spans": [{"text": line.get("text", ""), "effect": "vibrate"}]} for line in dialogue],
        }

    beautifier.beautify_scene.side_effect = concurrent_beautify

    published = []
    persist = Mock()
    notify = Mock()
    state = StoryState(persist=persist, notify_changed=notify, publish_audio_fn=published.append)
    state.text_beautifier = beautifier
    state.enable_scene_speech(mock_provider)

    state.set_scene("The hero steps forward.", [{"speaker": "Mara", "text": "Look ahead!", "kind": "speech"}])
    state._executor.shutdown(wait=True)

    assert state.narration == "The hero steps forward."
    assert state.narration_spans == [{"text": "The hero steps forward.", "effect": "glow"}]
    assert state.scene_dialogue == [
        {"speaker": "Mara", "text": "Look ahead!", "kind": "speech", "spans": [{"text": "Look ahead!", "effect": "vibrate"}]}
    ]
    assert persist.call_count >= 1
    notify.assert_called_once_with("latest")
    assert len(published) == 1
    assert published[0]["speaker"] == "Mara"
    assert published[0]["voice"] == "voice_alpha"
    assert published[0]["speech_generation"] == state.committed_scene_speech_generation


def test_set_scene_does_not_dispatch_speech_when_speech_disabled() -> None:
    beautifier = Mock()
    beautifier.beautify_scene.return_value = {
        "narration_spans": [],
        "dialogue": [{"speaker": "Mara", "text": "Hello", "spans": []}],
    }
    published = []
    state = StoryState(publish_audio_fn=published.append)
    state.text_beautifier = beautifier

    state.set_scene("The story starts.", [{"speaker": "Mara", "text": "Hello"}])

    assert state.narration == "The story starts."
    assert len(published) == 0
    assert state._active_speech_generation == 1
    assert state.committed_scene_speech_generation == 1


def test_dispatch_gathers_all_lines_before_publishing_all_at_once() -> None:
    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.return_value = "voice_alpha"

    line1_done = threading.Event()
    release_line2 = threading.Event()

    def staggered_synthesize(req):
        if req.text == "Line 1":
            line1_done.set()
        elif req.text == "Line 2":
            assert line1_done.wait(timeout=2.0)
            assert release_line2.wait(timeout=2.0)
        return SpeechSynthesisResult(
            audio_bytes=b"audio",
            mime_type="audio/mpeg",
            provider="mock",
            model="mock",
        )

    mock_provider.synthesize.side_effect = staggered_synthesize

    published = []
    state = StoryState(publish_audio_fn=published.append)
    state.enable_scene_speech(mock_provider)

    state.dispatch([
        {"speaker": "Alice", "text": "Line 1", "kind": "speech"},
        {"speaker": "Bob", "text": "Line 2", "kind": "speech"},
    ])

    assert line1_done.wait(timeout=2.0)
    # Line 1 is done synthesizing, but Line 2 is still in flight.
    # Lines must be gathered first: Line 1 must NOT be published yet!
    assert len(published) == 0

    release_line2.set()
    state._executor.shutdown(wait=True)

    assert len(published) == 2
    assert [p["speaker"] for p in published] == ["Alice", "Bob"]


def test_dispatch_cancellation_while_gathering_publishes_no_lines() -> None:
    mock_provider = MagicMock(spec=SpeechProvider)
    mock_provider.select_voice.return_value = "voice_alpha"

    line1_done = threading.Event()
    release_line2 = threading.Event()

    def staggered_synthesize(req):
        if req.text == "Line 1":
            line1_done.set()
        elif req.text == "Line 2":
            assert line1_done.wait(timeout=2.0)
            assert release_line2.wait(timeout=2.0)
        return SpeechSynthesisResult(
            audio_bytes=b"audio",
            mime_type="audio/mpeg",
            provider="mock",
            model="mock",
        )

    mock_provider.synthesize.side_effect = staggered_synthesize

    published = []
    state = StoryState(publish_audio_fn=published.append)
    state.enable_scene_speech(mock_provider)

    state.dispatch([
        {"speaker": "Alice", "text": "Line 1", "kind": "speech"},
        {"speaker": "Bob", "text": "Line 2", "kind": "speech"},
    ])

    assert line1_done.wait(timeout=2.0)
    assert len(published) == 0

    # Cancel while Line 2 is still in flight
    state.cancel()
    release_line2.set()
    state._executor.shutdown(wait=True)

    # Neither line should have been published
    assert len(published) == 0
