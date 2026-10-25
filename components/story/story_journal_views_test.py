"""Chunked, ordered completion/lore history preserves reusable prompt prefixes."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

import pytest

from components.notepad import NoteSnapshot
from components.story.story_recording_journal import RecordingEvent, StoryHistoryEvent, StoryRecordingJournal


def output(index: int) -> StoryHistoryEvent:
    return StoryHistoryEvent(
        event_id=f"output-{index}", action_id=f"action-{index}", kind="story_output",
        payload={"narration": f"OUTCOME_{index}"},
    )


@pytest.fixture
def journal(tmp_path: Path) -> Iterator[StoryRecordingJournal]:
    journal = StoryRecordingJournal(tmp_path / "story.sqlite")
    try:
        yield journal
    finally:
        journal.close()


def test_interleaved_stream_appends_until_high_water_then_drops_one_chunk(
    journal: StoryRecordingJournal,
) -> None:
    journal.complete_history([output(0)])
    journal.record_lore_read("responder", "rules.txt", "Lore document: rules.txt\n\nLORE_1")
    journal.complete_history([output(2)])
    first = journal.prompt_context("planner", 4, 2)
    assert first.index("OUTCOME_0") < first.index("LORE_1") < first.index("OUTCOME_2")
    journal.record_lore_read("recorder", "items.txt", "Lore document: items.txt\n\nLORE_3")
    full = journal.prompt_context("planner", 4, 2)
    assert full.startswith(first)
    assert len([line for line in full.splitlines() if line.startswith("{")]) == 4
    journal.complete_history([output(4)])
    rolled = journal.prompt_context("planner", 4, 2)
    assert "OUTCOME_0" not in rolled and "LORE_1" not in rolled
    assert rolled.index("OUTCOME_2") < rolled.index("LORE_3") < rolled.index("OUTCOME_4")
    journal.complete_history([output(4)])
    assert journal.prompt_context("planner", 4, 2) == rolled
    journal.complete_history([output(5)])
    assert journal.prompt_context("planner", 4, 2).startswith(rolled)
    journal.record_lore_read("responder", "places.txt", "Lore document: places.txt\n\nLORE_6")
    next_chunk = journal.prompt_context("planner", 4, 2)
    assert "OUTCOME_2" not in next_chunk and "LORE_3" not in next_chunk
    assert next_chunk.index("OUTCOME_4") < next_chunk.index("OUTCOME_5") < next_chunk.index("LORE_6")


def test_completing_an_older_pending_event_appends_to_the_history_prefix(journal: StoryRecordingJournal) -> None:
    event = RecordingEvent(
        event_id="old:recording", action_id="old", kind="completed_response",
        payload={"narration": "LATER_RECORDED_OUTCOME"},
    )
    journal.enqueue(event)
    journal.record_lore_read("responder", "rules.txt", "Lore document: rules.txt\n\nEARLIER_READ")
    before = journal.prompt_context("planner", 20, 10)
    journal.commit(event.event_id, NoteSnapshot(revision=0, schema_token="", notes={}, structured_notes={}))
    assert journal.prompt_context("planner", 20, 10) == before
    journal.complete_history([StoryHistoryEvent(
        event_id=event.event_id, action_id=event.action_id, kind="story_output", payload=event.payload,
    )])
    after = journal.prompt_context("planner", 20, 10)
    assert after.startswith(before)
    assert after.index("EARLIER_READ") < after.index("LATER_RECORDED_OUTCOME")


def test_lore_reads_are_shared_but_role_documents_remain_private(
    journal: StoryRecordingJournal,
) -> None:
    journal.record_lore_read("recorder", "rules.txt", "Lore document: rules.txt\n\nSHARED_LORE")
    journal.record_lore_read("recorder", "RECORDER.md", "Lore document: RECORDER.md\n\nPRIVATE_RECORDER")
    journal.record_lore_read("responder", "RESPONDER.md", "Lore document: RESPONDER.md\n\nPRIVATE_RESPONDER")
    responder = journal.prompt_context("responder", 20, 10)
    recorder = journal.prompt_context("recorder", 20, 10)
    planner = journal.prompt_context("planner", 20, 10)
    for prompt in (responder, recorder, planner):
        assert "SHARED_LORE" in prompt
    assert "PRIVATE_RESPONDER" in responder and "PRIVATE_RECORDER" not in responder
    assert "PRIVATE_RECORDER" in recorder and "PRIVATE_RESPONDER" not in recorder
    assert "PRIVATE" not in planner


def test_lore_errors_searches_and_listings_do_not_enter_history(
    journal: StoryRecordingJournal,
) -> None:
    original = journal.prompt_context("recorder", 20, 10)
    for result in (
        "Error: Missing document", "No theater is active", "Available lore documents:\n- rules.txt",
        "Lore documents in 'rules':\n- rules/magic.txt", "Lore search results for query 'magic':",
    ):
        journal.record_lore_read("responder", "rules.txt", result)
    assert journal.prompt_context("recorder", 20, 10) == original


def test_event_views_and_chunk_boundary_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "story.sqlite"
    journal = StoryRecordingJournal(path)
    try:
        for index in range(5):
            journal.complete_history([output(index)])
        journal.record_lore_read("recorder", "rules.txt", "Lore document: rules.txt\n\nPERSISTED_LORE")
        before = journal.prompt_context("planner", 4, 2)
    finally:
        journal.close()
    restored = StoryRecordingJournal(path)
    try:
        assert restored.prompt_context("planner", 4, 2) == before
        assert "OUTCOME_0" not in before and "OUTCOME_1" not in before
        assert "OUTCOME_2" in before and "PERSISTED_LORE" in before
    finally:
        restored.close()


def test_existing_completions_migrate_once_before_new_lore(tmp_path: Path) -> None:
    path = tmp_path / "story.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE story_completions (
                position INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE, event_json TEXT
            )"""
        )
        for index in range(2):
            event = output(index)
            connection.execute(
                "INSERT INTO story_completions (event_id, event_json) VALUES (?, ?)",
                (event.event_id, event.model_dump_json()),
            )
    journal = StoryRecordingJournal(path)
    try:
        journal.record_lore_read("responder", "rules.txt", "Lore document: rules.txt\n\nNEW_LORE")
        prompt = journal.prompt_context("planner", 20, 10)
        assert prompt.index("OUTCOME_0") < prompt.index("OUTCOME_1") < prompt.index("NEW_LORE")
        assert prompt.count("OUTCOME_0") == 1
    finally:
        journal.close()


def test_source_payload_and_stream_entry_roll_back_together(
    journal: StoryRecordingJournal, tmp_path: Path,
) -> None:
    with sqlite3.connect(tmp_path / "story.sqlite") as connection:
        connection.execute(
            """CREATE TRIGGER fail_sequence BEFORE UPDATE OF history_position ON recording_events
               BEGIN SELECT RAISE(ABORT, 'sequence unavailable'); END"""
        )
    before = journal.prompt_context("planner", 20, 10)
    with pytest.raises(sqlite3.IntegrityError, match="sequence unavailable"):
        journal.complete_history([output(1)])
    with pytest.raises(sqlite3.IntegrityError, match="sequence unavailable"):
        journal.record_lore_read("responder", "rules.txt", "Lore document: rules.txt\n\nUNCOMMITTED_LORE")
    assert journal.recent_history() == []
    assert journal.prompt_context("planner", 20, 10) == before
    with sqlite3.connect(tmp_path / "story.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM recording_events WHERE json_extract(event_json, '$.kind') = 'lore_read'").fetchone()[0] == 0


def test_concurrent_sources_have_one_total_order_without_missing_entries(
    journal: StoryRecordingJournal,
) -> None:
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = []
        for index in range(10):
            futures.append(executor.submit(journal.complete_history, [output(index)]))
            futures.append(executor.submit(journal.record_lore_read, "responder", "rules.txt", f"Lore document: rules.txt\n\nLORE_{index}"))
        for future in futures:
            future.result(5)
    prompt = journal.prompt_context("planner", 20, 10)
    assert len([line for line in prompt.splitlines() if line.startswith("{")]) == 20
    for index in range(10):
        assert prompt.count(f"OUTCOME_{index}\"") == 1
        assert prompt.count(f"LORE_{index}\"") == 1


def test_disabled_history_and_invalid_chunk_configuration(
    journal: StoryRecordingJournal,
) -> None:
    journal.complete_history([output(1)])
    assert journal.prompt_context("planner", 0, 1) == ""
    for limit, chunk in ((-1, 1), (20, 0), (20, 21)):
        with pytest.raises(ValueError, match="History requires"):
            journal.prompt_context("planner", limit, chunk)
