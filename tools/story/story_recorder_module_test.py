"""Recording ordering, durable publication, and restart recovery."""

from pathlib import Path
import sqlite3
from unittest.mock import Mock

import pytest

from components.canvas.game_runtime import GameAction
from components.canvas.game_state import GameState
from components.canvas_state import CanvasStateManager
from components.lore_library import LoreLibrary
from components.notepad import Notepad, NoteSnapshot
from components.theater_manager import Theater
from tools.story.story_handoffs import GameControlDecision, NarrationRequest
from tools.story.story_recorder_module import (
    MAX_RECORDER_READ_LORE_CALLS_PER_RUN,
    MAX_RECORDER_SEARCH_LORE_CALLS_PER_RUN,
    StoryRecorderModule,
    SubmitGameAction,
)
from tools.story.story_recording_journal import RecordingEvent, StoryRecordingJournal


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


def test_retrying_projection_preserves_narration_handoff_without_peer_triggering(
    pad: Notepad, tmp_path: Path
) -> None:
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    game_state = Mock(spec=GameState)
    game_state.story_snapshot.return_value = {"revision": 2, "state": {"tile": "B2"}}
    player_event = RecordingEvent(
        event_id="game-1",
        action_id="player-1",
        kind="committed_game_action",
        source_revision=0,
        payload={"action": {"name": "step"}, "receipt": {"revision": 1, "state": {"tile": "A2"}}},
    )
    narratron_action = GameAction(name="counter", arguments={"tile": "B2"}, revision=1)
    reconcile = Mock(
        side_effect=[
            {"Location": "Square A2"},
            RuntimeError("Transient projection failure"),
            {"Location": "Square B2"},
        ]
    )
    def control(
        event: RecordingEvent, snapshot: NoteSnapshot, submit: SubmitGameAction
    ) -> GameControlDecision:
        assert snapshot.notes["Location"] == "Square A2"
        receipt = submit(narratron_action)
        assert receipt == {"revision": 2, "state": {"tile": "B2"}}
        return GameControlDecision(
            narration_request=NarrationRequest(
                user_action="The warden blocks the corridor at B2.",
                nudge="Describe the counter-move.",
            )
        )

    control_mock = Mock(side_effect=control)
    recorder = StoryRecorderModule(
        pad,
        journal,
        reconcile,
        game_state=game_state,
        control_game=control_mock,
    )
    try:
        with pytest.raises(RuntimeError, match="Transient projection failure"):
            recorder.control_game(player_event)
        game_state.act.assert_called_once_with(narratron_action, actor="narratron")
        assert pad.snapshot().notes["Location"] == "Square A2"

        outcome = recorder.control_game(player_event)
        assert outcome.player_recorded_revision == 1
        assert outcome.final_recorded_revision == 2
        assert len(outcome.narratron_receipts) == 1
        assert outcome.narration_request is not None
        assert outcome.narration_triggered is False
        assert journal.is_game_control_completed("game-1")
        game_state.act.assert_called_once()
        control_mock.assert_called_once()
        assert reconcile.call_count == 3

        repeated = recorder.control_game(player_event)
        assert repeated == outcome
        assert reconcile.call_count == 3
    finally:
        journal.close()


def test_interrupted_controller_reuses_committed_receipt_and_rejects_conflicting_action(
    pad: Notepad, tmp_path: Path
) -> None:
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    game_state = Mock(spec=GameState)
    game_state.story_snapshot.return_value = {"revision": 2, "state": {"tile": "B2"}}
    player_event = RecordingEvent(
        event_id="game-1",
        action_id="player-1",
        kind="committed_game_action",
        source_revision=0,
        payload={"action": {"name": "step"}, "receipt": {"revision": 1, "state": {"tile": "A2"}}},
    )
    first_action = GameAction(name="counter", arguments={"tile": "B2"}, revision=1)
    conflicting_action = GameAction(name="counter", arguments={"tile": "C3"}, revision=1)
    attempts = 0

    def control(
        event: RecordingEvent, snapshot: NoteSnapshot, submit: SubmitGameAction
    ) -> GameControlDecision:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            submit(first_action)
            raise RuntimeError("Controller interrupted after committing game action")
        if attempts == 2:
            submit(conflicting_action)
            return GameControlDecision()
        assert submit(first_action) == {"revision": 2, "state": {"tile": "B2"}}
        return GameControlDecision()

    recorder = StoryRecorderModule(
        pad,
        journal,
        Mock(return_value={}),
        game_state=game_state,
        control_game=control,
    )
    try:
        with pytest.raises(RuntimeError, match="Controller interrupted"):
            recorder.control_game(player_event)
        game_state.act.assert_called_once_with(first_action, actor="narratron")

        with pytest.raises(ValueError, match="different committed Narratron action"):
            recorder.control_game(player_event)
        game_state.act.assert_called_once()

        outcome = recorder.control_game(player_event)
        assert len(outcome.narratron_receipts) == 1
        assert outcome.narration_triggered is False
        assert journal.pending() == []
        game_state.act.assert_called_once()
    finally:
        journal.close()


def test_completed_response_and_narratron_receipt_cannot_enter_game_control(
    pad: Notepad, tmp_path: Path
) -> None:
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    recorder = StoryRecorderModule(pad, journal, Mock(return_value={}))
    narratron_event = RecordingEvent(
        event_id="game-1:narratron:1",
        action_id="player-1",
        source_event_ids=("game-1",),
        kind="committed_game_action",
        source_revision=0,
        payload={"actor": "narratron", "action": {"name": "counter"}, "receipt": {"revision": 2}},
    )
    try:
        with pytest.raises(ValueError, match="Completed responses cannot enter game control"):
            recorder.control_game(response())
        with pytest.raises(ValueError, match="Narratron game receipts cannot recursively"):
            recorder.control_game(narratron_event)
    finally:
        journal.close()


def test_recorder_instructions_combine_source_template_and_pinned_recorder_md(
    pad: Notepad, tmp_path: Path
) -> None:
    theater = Mock(spec=Theater)
    theater.theater_id = "recorder-test"
    theater.lore_documents.return_value = [
        "RECORDER.md",
        "RESPONDER.md",
        "PLANNER.md",
        "rules/inventory.txt",
    ]
    contents = {
        "RECORDER.md": "RECORDER PRIORITY: verify brass key usage against archive rules.",
        "RESPONDER.md": "RESPONDER PRIORITY: narrate sensory details.",
        "PLANNER.md": "PLANNER SECRET: archivist counter-plan.",
        "rules/inventory.txt": "Brass keys snap when forced.",
    }
    theater.read_lore_document.side_effect = contents.__getitem__
    lore_library = LoreLibrary(theater)
    game_state = Mock(spec=GameState)
    game_state.program = Mock()
    game_state.story_snapshot.return_value = {
        "revision": 2,
        "state": {"square": "Courtyard"},
        "instructions": "Advance the patrol when the bell rings.",
        "actions": {"advance_patrol": {"description": "Move patrol"}},
    }
    journal = StoryRecordingJournal(tmp_path / "recording.sqlite")
    recorder = StoryRecorderModule(
        pad,
        journal,
        Mock(return_value={}),
        lore_library=lore_library,
        game_state=game_state,
        style="grounded mystery",
    )
    try:
        instruction = recorder._build_recorder_instruction()
        assert "# Role & Mission" in instruction
        assert "# Phase 1 — Factual Recording Rules" in instruction
        assert "# Phase 2 — Game Control Rules" in instruction
        assert "grounded mystery" in instruction
        assert '"Location"' in instruction
        assert '"Inventory"' in instruction
        assert '"Ambush"' not in instruction
        assert "# Pinned Recorder Instructions (RECORDER.md)" in instruction
        assert contents["RECORDER.md"] in instruction
        assert contents["RESPONDER.md"] not in instruction
        assert contents["PLANNER.md"] not in instruction
        assert "RESPONDER.md" in instruction  # Mentioned only as boundary description
        assert "private" in recorder.read_lore("RESPONDER.md")
        assert "private" in recorder.read_lore("PLANNER.md")
        assert contents["RECORDER.md"] in recorder.read_lore("RECORDER.md")

        turn_prompt = recorder._build_recorder_turn_prompt(
            response(), pad.snapshot(), phase="control_game"
        )
        assert "# Recorder Work (control_game)" in turn_prompt
        assert "Advance the patrol when the bell rings." in turn_prompt
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

