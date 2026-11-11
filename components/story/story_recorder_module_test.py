"""Recording ordering, durable publication, and restart recovery."""

from pathlib import Path
import sqlite3
from unittest.mock import Mock, patch

import pytest

from components.canvas_state import CanvasStateManager
from components.lore_library import LoreLibrary
from components.notepad import Notepad, NoteSnapshot
from components.theater_manager import Theater
from components.story.story_handoffs import GameControlDecision, NarrationRequest
from components.story.story_recorder_module import (
    MAX_RECORDER_READ_LORE_CALLS_PER_RUN,
    MAX_RECORDER_SEARCH_LORE_CALLS_PER_RUN,
    StoryRecorderModule,
)
from components.story.story_recording_journal import RecordingEvent, StoryRecordingJournal


@pytest.fixture
def pad(tmp_path: Path) -> Notepad:
    theater = Mock(spec=Theater)
    theater.config.return_value = {"story_planning": {"adventure_mode": True, "enforce_structured": False}}
    theater.directory.return_value = tmp_path
    theater.read_planning_schema.return_value = {
        "Location": {"owner": "recorder", "initial": "Gate"},
        "Inventory": {"owner": "recorder", "initial": "Key"},
        "Ambush": {"owner": "planner", "initial": "Wait"},
    }
    return Notepad(theater, canvas_manager=Mock(spec=CanvasStateManager))


def response(event_id: str = "response-1") -> RecordingEvent:
    return RecordingEvent(
        event_id=event_id,
        action_id=f"action-{event_id}",
        source_revision=0,
        kind="completed_response",
        payload={"narration": "The key breaks in the courtyard."},
    )


def test_recording_commits_once_and_recovers_after_restart(pad: Notepad, tmp_path: Path) -> None:
    path = tmp_path / "recording.sqlite"
    journal = StoryRecordingJournal(path)
    reconcile = Mock(return_value={"Location": "Courtyard", "Inventory": "Broken key"})
    recorder = StoryRecorderModule(pad, journal, reconcile)
    try:
        assert recorder.record(response()) == 1
        assert recorder.record(response()) == 1
        reconcile.assert_called_once()
        assert journal.pending() == []
        assert journal.latest_snapshot() == pad.snapshot()
    finally:
        journal.close()
    pad.configure_schema({
        "Location": {"owner": "recorder", "initial": "Gate"},
        "Inventory": {"owner": "recorder", "initial": "Key"},
        "Ambush": {"owner": "planner", "initial": "Wait"},
    })
    restored = StoryRecordingJournal(path)
    try:
        restarted = StoryRecorderModule(pad, restored, reconcile)
        assert pad.snapshot().notes["Inventory"] == "Broken key"
        assert restarted.record(response()) == 1
        reconcile.assert_called_once()
    finally:
        restored.close()


def test_failed_reconciliation_blocks_successor_until_retry(pad: Notepad, tmp_path: Path) -> None:
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    reconcile = Mock(side_effect=[RuntimeError("Temporary failure"), {"Inventory": "Broken key"}, {}])
    recorder = StoryRecorderModule(pad, journal, reconcile)
    initial = pad.snapshot()
    try:
        with pytest.raises(RuntimeError, match="Temporary failure"):
            recorder.record(response())
        assert pad.snapshot() == initial
        with pytest.raises(ValueError, match="earlier event"):
            recorder.record(response("response-2"))
        assert [event.event_id for event in journal.pending()] == ["response-1", "response-2"]
        recorder.retry_pending()
        assert journal.pending() == []
        assert journal.recorded_revision("response-2") == 1
        assert reconcile.call_count == 3
    finally:
        journal.close()


def test_invalid_recording_batch_preserves_pending_event(pad: Notepad, tmp_path: Path) -> None:
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    recorder = StoryRecorderModule(
        pad, journal, Mock(return_value={"Location": "Courtyard", "Ambush": "Now"}),
    )
    initial = pad.snapshot()
    try:
        with pytest.raises(ValueError, match="cannot write"):
            recorder.record(response())
        assert pad.snapshot() == initial
        assert journal.latest_snapshot() is None
        assert journal.pending() == [response()]
    finally:
        journal.close()


def test_database_failure_rolls_back_notes_and_progress(pad: Notepad, tmp_path: Path) -> None:
    path = tmp_path / "recording.sqlite"
    journal = StoryRecordingJournal(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TRIGGER fail_recording BEFORE UPDATE ON recording_events
               BEGIN SELECT RAISE(ABORT, 'simulated commit failure'); END"""
        )
    recorder = StoryRecorderModule(pad, journal, Mock(return_value={"Location": "Courtyard"}))
    initial = pad.snapshot()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="simulated"):
            recorder.record(response())
        assert pad.snapshot() == initial
        assert journal.latest_snapshot() is None
        assert journal.pending() == [response()]
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TRIGGER fail_recording")
        recorder.retry_pending()
        assert pad.snapshot().notes["Location"] == "Courtyard"
        assert journal.pending() == []
    finally:
        journal.close()


def test_no_change_recording_persists_progress_at_revision_zero(pad: Notepad, tmp_path: Path) -> None:
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    reconcile = Mock(return_value={})
    recorder = StoryRecorderModule(pad, journal, reconcile)
    try:
        assert recorder.record(response()) == 0
        assert recorder.record(response()) == 0
        assert journal.recorded_revision("response-1") == 0
        assert journal.pending() == []
        reconcile.assert_called_once()
    finally:
        journal.close()


@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("receipt", [False, True])
def test_recording_ignores_narration_request_and_commits_facts(
    pad: Notepad, tmp_path: Path, saved: bool, receipt: bool,
) -> None:
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    event = response()
    if receipt:
        event = RecordingEvent(
            event_id="game-1:narratron:1", action_id="player-1",
            source_event_ids=("game-1",), kind="committed_game_action",
            payload={"actor": "narratron", "receipt": {"revision": 2}},
        )
    decision = GameControlDecision(
        factual_updates={"Inventory": "Broken key"},
        narration_request=NarrationRequest(user_action="Narrate again", brief="The key breaks."),
    )
    recorder = StoryRecorderModule(pad, journal)
    try:
        if saved:
            journal.enqueue(event)
            journal.save_recorder_decision(event.event_id, decision)
        with patch.object(recorder, "_run_model", return_value=decision) as run_model:
            assert recorder.record(event) == 1
            assert recorder.record(event) == 1
            assert run_model.call_count == (0 if saved else 1)
        assert pad.snapshot().notes["Inventory"] == "Broken key"
        assert journal.pending() == []
        assert journal.recorded_revision(event.event_id) == 1
        stored = journal.recorder_decision(event.event_id)
        assert stored is not None
        assert stored.factual_updates == decision.factual_updates
        if not saved:
            assert stored.narration_request is None
    finally:
        journal.close()


def test_event_identity_cannot_be_reused_with_different_payload(pad: Notepad, tmp_path: Path) -> None:
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    recorder = StoryRecorderModule(pad, journal, Mock(return_value={}))
    try:
        recorder.record(response())
        changed = response().model_copy(update={"payload": {"narration": "Different outcome"}})
        with pytest.raises(ValueError, match="different content"):
            recorder.record(changed)
    finally:
        journal.close()


def test_durable_commit_before_live_publication_is_recovered(pad: Notepad, tmp_path: Path) -> None:
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    event = response()
    journal.enqueue(event)
    source = pad.snapshot()

    def commit_then_interrupt(snapshot: NoteSnapshot) -> None:
        journal.commit(event.event_id, snapshot)
        raise RuntimeError("Interrupted before live publication")

    try:
        with pytest.raises(RuntimeError, match="Interrupted"):
            pad.publish_role_updates(
                "recorder", {"Location": "Courtyard"}, source=source,
                persist=commit_then_interrupt,
            )
        assert pad.snapshot() == source
        reconcile = Mock()
        recorder = StoryRecorderModule(pad, journal, reconcile)
        assert recorder.record(event) == 1
        assert pad.snapshot().notes["Location"] == "Courtyard"
        reconcile.assert_not_called()
    finally:
        journal.close()


def test_pending_game_receipt_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "recording.sqlite"
    event = RecordingEvent(
        event_id="game-1", action_id="player-1", kind="committed_game_action",
        source_revision=2,
        payload={"action": {"name": "move"}, "receipt": {"revision": 3, "state": {"square": 4}}},
    )
    journal = StoryRecordingJournal(path)
    journal.enqueue(event)
    journal.close()
    restored = StoryRecordingJournal(path)
    try:
        assert restored.pending() == [event]
    finally:
        restored.close()


def test_recorder_instructions_combine_source_template_and_pinned_recorder_md(
    pad: Notepad, tmp_path: Path
) -> None:
    theater = Mock(spec=Theater)
    theater.theater_id = "recorder-test"
    theater.lore_documents.return_value = [
        "RECORDER.md",
        "RESPONDER.md",
        "PLANNER.md",
        "GAME.md",
        "rules/inventory.txt",
    ]
    contents = {
        "RECORDER.md": "RECORDER PRIORITY: verify brass key usage against archive rules.",
        "RESPONDER.md": "RESPONDER PRIORITY: narrate sensory details.",
        "PLANNER.md": "PLANNER SECRET: archivist counter-plan.",
        "GAME.md": "GAME SECRET: play Ivory pieces conservatively.",
        "rules/inventory.txt": "Brass keys snap when forced.",
    }
    theater.read_lore_document.side_effect = contents.__getitem__
    lore_library = LoreLibrary(theater)
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    recorder = StoryRecorderModule(
        pad,
        journal,
        Mock(return_value={}),
        lore_library=lore_library,
        style="grounded mystery",
    )
    try:
        instruction = recorder._build_recorder_instruction()
        assert "# Role & Mission" in instruction
        assert "# Factual Recording Rules" in instruction
        assert "Game Control Rules" not in instruction
        assert "grounded mystery" in instruction
        assert '"Location"' in instruction
        assert '"Inventory"' in instruction
        assert '"Ambush"' not in instruction
        assert "# Pinned Recorder Instructions (RECORDER.md)" in instruction
        assert contents["RECORDER.md"] in instruction
        assert contents["RESPONDER.md"] not in instruction
        assert contents["PLANNER.md"] not in instruction
        assert contents["GAME.md"] not in instruction
        assert "RESPONDER.md" in instruction  # Mentioned only as boundary description
        assert "GAME.md" in instruction  # Mentioned only as boundary description
        assert "private" in recorder.read_lore("RESPONDER.md")
        assert "private" in recorder.read_lore("PLANNER.md")
        assert "private" in recorder.read_lore("GAME.md")
        assert contents["RECORDER.md"] in recorder.read_lore("RECORDER.md")

        turn_prompt = recorder._build_recorder_turn_prompt(
            response(), pad.snapshot()
        )
        assert "# Recorder Work (record)" in turn_prompt
        assert "- [recorder] Location: Gate" in turn_prompt
        assert "- [planner] Ambush: Wait" in turn_prompt
    finally:
        journal.close()


def test_recorder_lore_budgets_and_reset(pad: Notepad, tmp_path: Path) -> None:
    theater = Mock(spec=Theater)
    theater.theater_id = "recorder-budget"
    theater.lore_documents.return_value = ["rules.txt"]
    theater.read_lore_document.return_value = " Brass key rules."
    lore_library = LoreLibrary(theater)
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    recorder = StoryRecorderModule(
        pad, journal, Mock(return_value={}), lore_library=lore_library,
    )
    try:
        for _ in range(MAX_RECORDER_READ_LORE_CALLS_PER_RUN):
            assert "Brass key rules." in recorder.read_lore("rules.txt")
        assert "Maximum recorder read_lore limit" in recorder.read_lore("rules.txt")

        for _ in range(MAX_RECORDER_SEARCH_LORE_CALLS_PER_RUN):
            assert "rules.txt" in recorder.search_lore("Brass")
        assert "Maximum recorder search_lore limit" in recorder.search_lore("Brass")

        recorder.reset_lore_call_counts()
        assert "Brass key rules." in recorder.read_lore("rules.txt")
        assert "rules.txt" in recorder.search_lore("Brass")
    finally:
        journal.close()


def test_recorder_debug_logging(pad: Notepad, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    reconcile = Mock(return_value={"Location": "Courtyard"})
    try:
        with caplog.at_level("DEBUG"):
            recorder = StoryRecorderModule(pad, journal, reconcile)
            assert recorder.record(response()) == 1
            assert recorder.record(response()) == 1
        assert "[StoryRecorderModule] Initialized" in caplog.text
        assert "[StoryRecorderModule] record() called for event response-1" in caplog.text
        assert "[StoryRecorderModule] Reconciled 1 update(s) for event response-1" in caplog.text
        assert "[StoryRecorderModule] Published recorded revision 1 for event response-1" in caplog.text
        assert "[StoryRecorderModule] Event response-1 already recorded at revision 1" in caplog.text
    finally:
        journal.close()
