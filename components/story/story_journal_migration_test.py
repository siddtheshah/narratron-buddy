"""Consolidation preserves canonical payloads, context ordering, and progress."""

from pathlib import Path
import sqlite3

import pytest

from components.notepad import NoteSnapshot
from components.story.story_recording_journal import (
    LoreReadRecord, RecordedNotes, RecordingEvent, StoryHistoryEvent, StoryRecordingJournal,
)


def seed_legacy_journals(path: Path, *, conflicting_output: bool = False) -> tuple[RecordingEvent, RecordingEvent]:
    response = RecordingEvent(
        event_id="turn:recording", action_id="turn", kind="completed_response",
        source_revision=3, payload={"narration": "RECORDED_NARRATION"},
    )
    game = RecordingEvent(
        event_id="game:recording", action_id="game", kind="committed_game_action",
        source_revision=4, payload={"actor": "player", "action": {"name": "trade"}, "receipt": {"revision": 2}},
    )
    pending = RecordingEvent(
        event_id="pending:recording", action_id="pending", kind="completed_response",
        payload={"narration": "UNRECORDED_OUTPUT"},
    )
    input_event = StoryHistoryEvent(
        event_id="turn:input", action_id="turn", kind="player_action",
        source_event_ids=("turn:output",), payload={"action": "Try the key"},
    )
    output_event = StoryHistoryEvent(
        event_id="turn:output", action_id="turn", kind="story_output",
        source_event_ids=("turn:input",),
        payload={"narration": "CONFLICT"} if conflicting_output else response.payload,
    )
    game_event = StoryHistoryEvent(
        event_id="game:input", action_id="game", kind="game_action",
        payload={**game.payload, "action_name": "trade", "narrative_brief": "A key changed hands."},
    )
    game_output = StoryHistoryEvent(
        event_id="game:output", action_id="game", kind="story_output",
        source_event_ids=("game:input",), payload={"narration": "GAME_NARRATION"},
    )
    read = LoreReadRecord(event_id="read", reader="recorder", document="rules.txt", content="Lore document: rules.txt\n\nSHARED_LORE")
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE recording_events (
                position INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE,
                event_json TEXT, recorded_revision INTEGER
            )"""
        )
        connection.execute("CREATE TABLE story_completions (position INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE, event_json TEXT)")
        connection.execute("CREATE TABLE story_lore_reads (event_id TEXT PRIMARY KEY, event_json TEXT)")
        connection.execute("CREATE TABLE story_context_events (position INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, event_id TEXT)")
        connection.execute("CREATE TABLE recorded_notes (singleton INTEGER PRIMARY KEY, snapshot_json TEXT)")
        for event, revision in ((response, 4), (game, 5), (pending, None)):
            connection.execute(
                "INSERT INTO recording_events (event_id, event_json, recorded_revision) VALUES (?, ?, ?)",
                (event.event_id, event.model_dump_json(), revision),
            )
        for event in (input_event, output_event, game_event, game_output):
            connection.execute("INSERT INTO story_completions (event_id, event_json) VALUES (?, ?)", (event.event_id, event.model_dump_json()))
        connection.execute("INSERT INTO story_lore_reads (event_id, event_json) VALUES (?, ?)", (read.event_id, read.model_dump_json()))
        for source, event_id in (
            ("completion", input_event.event_id), ("lore_read", read.event_id),
            ("completion", output_event.event_id), ("completion", game_event.event_id),
            ("completion", game_output.event_id),
        ):
            connection.execute("INSERT INTO story_context_events (source, event_id) VALUES (?, ?)", (source, event_id))
        connection.execute(
            "INSERT INTO recorded_notes (singleton, snapshot_json) VALUES (1, ?)",
            (RecordedNotes.from_snapshot(NoteSnapshot(revision=5, schema_token="schema", notes={}, structured_notes={})).model_dump_json(),),
        )
    return response, game


def test_legacy_tables_merge_without_duplicate_payloads_and_keep_context_order(tmp_path: Path) -> None:
    path = tmp_path / "story.sqlite"
    response, game = seed_legacy_journals(path)
    journal = StoryRecordingJournal(path)
    try:
        history = journal.recent_history(20)
        assert [event.event_id for event in history] == ["turn:input", "read", "turn:recording", "game:recording", "game:output"]
        assert history[0].source_event_ids == (response.event_id,)
        assert history[4].source_event_ids == (game.event_id,)
        assert history[3].narrative_brief == "A key changed hands."
        assert history[3].payload == game.payload
        assert journal.recorded_revision(response.event_id) == 4
        assert journal.recorded_revision(game.event_id) == 5
        assert [event.event_id for event in journal.pending()] == ["pending:recording"]
        snapshot = journal.latest_snapshot()
        assert snapshot is not None and snapshot.revision == 5
        context = journal.prompt_context("planner", 4, 2)
        assert "SHARED_LORE" not in context
        assert context.index("RECORDED_NARRATION") < context.index("A key changed hands") < context.index("GAME_NARRATION")
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM recording_events").fetchone()[0] == 6
            for event in (response, game):
                row = connection.execute("SELECT event_json FROM recording_events WHERE event_id = ?", (event.event_id,)).fetchone()
                assert RecordingEvent.model_validate_json(row[0]) == event
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            assert not tables & {"story_completions", "story_lore_reads", "story_context_events"}
    finally:
        journal.close()
    restored = StoryRecordingJournal(path)
    try:
        assert restored.recent_history(20) == history
        assert restored.prompt_context("planner", 4, 2) == context
    finally:
        restored.close()


def test_conflicting_legacy_copy_rolls_back_schema_and_payload_migration(tmp_path: Path) -> None:
    path = tmp_path / "story.sqlite"
    response, _ = seed_legacy_journals(path, conflicting_output=True)
    with pytest.raises(ValueError, match="different content"):
        StoryRecordingJournal(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM recording_events").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM story_context_events").fetchone()[0] == 5
        columns = {row[1] for row in connection.execute("PRAGMA table_info(recording_events)")}
        assert "history_position" not in columns
        row = connection.execute("SELECT event_json FROM story_completions WHERE event_id = 'turn:output'").fetchone()
        legacy_output = StoryHistoryEvent.model_validate_json(row[0]).model_copy(update={"payload": response.payload})
        connection.execute("UPDATE story_completions SET event_json = ? WHERE event_id = 'turn:output'", (legacy_output.model_dump_json(),))
    recovered = StoryRecordingJournal(path)
    try:
        assert len(recovered.recent_history()) == 5
    finally:
        recovered.close()
