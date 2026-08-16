from pathlib import Path

from components.scene_speech import SEED_CHARACTER_VOICES, SceneSpeechDispatcher, speaker_key


def test_speaker_key_normalizes_equivalent_display_names():
    assert speaker_key("  Mara   Venn ") == "mara venn"


def test_voice_assignment_is_stable_and_uses_an_unused_preset(tmp_path: Path):
    assignments = {"existing": SEED_CHARACTER_VOICES[0]}
    persisted = []
    dispatcher = SceneSpeechDispatcher(
        theater_id="lantern-house",
        output_dir=tmp_path,
        assignments=assignments,
        persist_assignments=lambda: persisted.append(True),
        publish_audio=lambda _message: None,
    )

    voice = dispatcher._voice_for("Mara Venn")

    assert voice in SEED_CHARACTER_VOICES
    assert voice != SEED_CHARACTER_VOICES[0]
    assert assignments["mara venn"] == voice
    assert dispatcher._voice_for("  mara   venn ") == voice
    assert persisted == [True]
