"""Bounded, ordered, durable history contains completed story events only."""

from pathlib import Path
import sqlite3

import pytest

from components.notepad import NoteSnapshot
from components.story.story_recording_journal import RecordingEvent, StoryHistoryEvent, StoryRecordingJournal


def output(index: int) -> StoryHistoryEvent:
    return StoryHistoryEvent(
        event_id=f"output-{index}", action_id=f"action-{index}", kind="story_output",
        payload={"narration": f"Outcome {index}"},
    )


def test_recent_completions_are_bounded_ordered_and_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "story.sqlite"
    journal = StoryRecordingJournal(path)
    try:
        journal.complete_history([output(index) for index in range(25)])
        assert journal.recent_history() == [output(index) for index in range(5, 25)]
        assert journal.recent_history(2) == [output(23), output(24)]
        assert journal.recent_history(0) == []
        with pytest.raises(ValueError, match="negative"):
            journal.recent_history(-1)
        journal.complete_history([output(23), output(24)])
        assert len(journal.recent_history(100)) == 25
    finally:
        journal.close()
    restored = StoryRecordingJournal(path)
    try:
        assert restored.recent_history(2) == [output(23), output(24)]
    finally:
        restored.close()


def test_conflicting_completion_rolls_back_the_whole_stage(tmp_path: Path) -> None:
    journal = StoryRecordingJournal(tmp_path / "story.sqlite")
    try:
        journal.complete_history([output(1)])
        conflict = output(1).model_copy(update={"payload": {"narration": "Contradiction"}})
        with pytest.raises(ValueError, match="different content"):
            journal.complete_history([output(2), conflict])
        assert journal.recent_history() == [output(1)]
    finally:
        journal.close()


def test_game_history_preserves_responder_isolation(tmp_path: Path) -> None:
    journal = StoryRecordingJournal(tmp_path / "story.sqlite")
    try:
        event = RecordingEvent(
            event_id="game-1", action_id="game-1", kind="committed_game_action",
            payload={"actor": "player", "action": {"name": "trade"}, "receipt": {"state": {"PRIVATE_GAME_SECRET": 42}}},
        )
        journal.enqueue(event)
        journal.commit(event.event_id, NoteSnapshot(revision=0, schema_token="", notes={}, structured_notes={}))
        journal.complete_history([StoryHistoryEvent(
            event_id="game-1", action_id="game-1", kind="game_action",
            narrative_brief="The merchant hands over the key.", payload=event.payload,
        )])
        context = journal.prompt_context("responder", 20, 10)
        assert "The merchant hands over the key" in context
        assert "PRIVATE_GAME_SECRET" not in context
        assert "PRIVATE_GAME_SECRET" in journal.prompt_context("recorder", 20, 10)
        assert "PRIVATE_GAME_SECRET" in journal.prompt_context("planner", 20, 10)
    finally:
        journal.close()


def test_history_reuses_canonical_response_and_filters_views(tmp_path: Path) -> None:
    path = tmp_path / "story.sqlite"
    journal = StoryRecordingJournal(path)
    event = RecordingEvent(
        event_id="turn:recording", action_id="turn", kind="completed_response",
        source_revision=3, payload={"narration": "A resolved outcome"},
    )
    history = StoryHistoryEvent(
        event_id=event.event_id, action_id=event.action_id, kind="story_output", payload=event.payload,
    )
    snapshot = NoteSnapshot(revision=4, schema_token="schema", notes={}, structured_notes={})
    try:
        journal.enqueue(event)
        with pytest.raises(ValueError, match="factual recording"):
            journal.complete_history([history])
        assert journal.pending() == [event]
        assert journal.recent_history() == []
        journal.commit(event.event_id, snapshot)
        journal.complete_history([history])
        journal.record_lore_read("responder", "rules.txt", "Lore document: rules.txt\n\nA reusable rule")
        journal.complete_history([history])
        journal.enqueue(event)
        assert journal.pending() == []
        assert journal.latest_snapshot() == snapshot
        assert journal.recorded_revision(event.event_id) == 4
        assert journal.recent_history(kinds=("story_output",)) == [history]
        assert len(journal.recent_history(kinds=("lore_read",))) == 1
        assert journal.recent_history(kinds=()) == []
        with sqlite3.connect(path) as connection:
            rows = connection.execute(
                "SELECT event_json FROM recording_events WHERE event_id = ?", (event.event_id,),
            ).fetchall()
            assert len(rows) == 1
            assert RecordingEvent.model_validate_json(rows[0][0]) == event
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            assert not tables & {"story_completions", "story_lore_reads", "story_context_events"}
    finally:
        journal.close()
