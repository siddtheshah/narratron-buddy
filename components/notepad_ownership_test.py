"""Ownership boundaries for the recorder/planner split."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import JsonValue

from components.canvas_state import CanvasStateManager
from components.notepad import Notepad, StaleNoteSnapshot, parse_planning_schema
from components.theater_manager import Theater
from components.story.story_staged_notes import StagedStoryNotes


@pytest.fixture
def pad(tmp_path: Path) -> Notepad:
    theater = Mock(spec=Theater)
    theater.config.return_value = {"story_planning": {"adventure_mode": True, "enforce_structured": False}}
    theater.directory.return_value = tmp_path
    theater.read_planning_schema.return_value = {
        "Location": {"owner": "recorder", "initial": "Gate"},
        "Stats": {
            "owner": "recorder",
            "fields": {"hp": "Hit points"},
            "initial": {"hp": "10"},
        },
        "Ambush": {"owner": "planner", "initial": "Wait for darkness"},
    }
    canvas = Mock(spec=CanvasStateManager)
    return Notepad(theater, canvas_manager=canvas, on_change=Mock())


def test_owner_is_preserved_in_wrapped_schema_and_tool_contract(pad: Notepad) -> None:
    definitions = parse_planning_schema(
        {"stickies": {"Location": {"owner": "recorder"}}}
    )
    assert definitions["Location"]["owner"] == "recorder"
    assert json.loads(pad.for_role("recorder").check_schema("Location"))["owner"] == "recorder"


@pytest.mark.parametrize("owner", [None, "", "responder", ["recorder"], 1])
def test_invalid_explicit_owner_is_rejected(owner: JsonValue) -> None:
    with pytest.raises(ValueError, match="owner must be"):
        parse_planning_schema({"Location": {"owner": owner}})


def test_legacy_notes_require_explicit_migration_before_role_binding(pad: Notepad) -> None:
    pad.configure_schema({"Location": {"initial": "Gate"}})
    assert pad.sticky_definitions["Location"]["owner"] is None
    with pytest.raises(ValueError, match="explicit owner"):
        pad.for_role("recorder")
    # Existing two-role and ordinary narration callers remain compatible.
    assert pad.update_sticky_note("Location", "Courtyard").startswith("Updated")


def test_roles_cannot_write_peer_unknown_or_unowned_topics(pad: Notepad) -> None:
    pad.enforce_structured = True
    recorder = pad.for_role("recorder")
    planner = pad.for_role("planner")
    before = pad.export_state()
    for tools, topic in ((recorder, "Ambush"), (planner, "Location"), (planner, "Unknown")):
        assert tools.check_schema(topic).startswith("Error:")
        assert tools.update_sticky_note(topic, "Changed").startswith("Error:")
    assert pad.export_state() == before
    assert pad.get_recently_changed_topics() == frozenset()

    pad.configure_schema({"Location": {"initial": "Gate"}})
    before = pad.export_state()
    assert recorder.update_sticky_note("Location", "Courtyard").startswith("Error:")
    assert pad.export_state() == before


def test_owned_writes_validate_fields_even_in_unstructured_mode(pad: Notepad) -> None:
    recorder = pad.for_role("recorder")
    before = pad.export_state()
    assert recorder.update_sticky_note("Stats", '{"hp":9}').startswith("Error:")
    assert recorder.update_sticky_note("Stats", '{"mp":"9"}').startswith("Error:")
    assert pad.export_state() == before
    assert recorder.update_sticky_note("Stats", '{"hp":"9"}').startswith("Updated")
    assert pad.get_present_structured_sticky_notes()["Stats"] == {"hp": "9"}
    assert pad.revision == 1
    assert recorder.update_sticky_note("Stats", '{"hp":"9"}').startswith("Unchanged")
    assert pad.revision == 1
    assert pad.for_role("planner").update_sticky_note(" Ambush ", "At dawn").startswith("Updated")
    assert pad.revision == 2


def test_existing_role_tools_follow_reassigned_ownership(pad: Notepad) -> None:
    recorder = pad.for_role("recorder")
    pad.configure_schema({"Location": {"owner": "planner", "initial": "Gate"}})
    assert recorder.update_sticky_note("Location", "Courtyard").startswith("Error:")
    assert pad.for_role("planner").update_sticky_note("Location", "Courtyard").startswith("Updated")


def test_invalid_schema_replacement_preserves_existing_state(pad: Notepad) -> None:
    before = pad.export_state()
    with pytest.raises(ValueError, match="owner must be"):
        pad.configure_schema({"Location": {"owner": "responder"}})
    assert pad.export_state() == before
    assert pad.for_role("recorder").update_sticky_note("Location", "Courtyard").startswith("Updated")


def test_batch_publication_is_atomic_and_notifies_once(pad: Notepad) -> None:
    source = pad.snapshot()
    observed: list[dict[str, str]] = []
    pad.on_change = lambda: observed.append(pad.snapshot().notes)
    revision = pad.publish_role_updates(
        "recorder", {"Location": "Courtyard", "Stats": '{"hp":"9"}'}, source=source,
    )
    assert revision == source.revision + 1
    assert observed == [{
        "Location": "Courtyard", "Stats": "hp: 9", "Ambush": "Wait for darkness",
    }]
    assert source.notes["Location"] == "Gate"
    assert source.structured_notes["Stats"] == {"hp": "10"}


@pytest.mark.parametrize(
    "invalid", [{"Stats": '{"hp":9}'}, {"Ambush": "At dawn"}, {"Location ": "Other"}],
)
def test_invalid_batch_never_publishes_its_valid_prefix(
    pad: Notepad, invalid: dict[str, str],
) -> None:
    source = pad.snapshot()
    changed = Mock()
    pad.on_change = changed
    with pytest.raises(ValueError):
        pad.publish_role_updates(
            "recorder", {"Location": "Courtyard", **invalid}, source=source,
        )
    assert pad.snapshot() == source
    changed.assert_not_called()


def test_publication_rejects_stale_facts_and_ownership(pad: Notepad) -> None:
    source = pad.snapshot()
    pad.for_role("recorder").update_sticky_note("Location", "Courtyard")
    with pytest.raises(StaleNoteSnapshot):
        pad.publish_role_updates("planner", {"Ambush": "At dawn"}, source=source)
    source = pad.snapshot()
    # Ownership-only changes need to invalidate snapshots even without a content revision.
    pad.sticky_definitions["Ambush"]["owner"] = "recorder"
    assert pad.revision == source.revision
    with pytest.raises(StaleNoteSnapshot):
        pad.publish_role_updates("planner", {"Ambush": "At dawn"}, source=source)


def test_unchanged_batch_preserves_revision(pad: Notepad) -> None:
    source = pad.snapshot()
    assert pad.publish_role_updates(
        "recorder", {"Location": "Gate", "Stats": '{"hp":"10"}'}, source=source,
    ) == source.revision
    assert pad.snapshot() == source


def test_consume_responder_snapshot_captures_notes_revision_and_changed_topics_atomically(
    pad: Notepad,
) -> None:
    pad.for_role("recorder").update_sticky_note("Location", "Courtyard")
    snapshot, changed = pad.consume_responder_snapshot()
    assert snapshot.revision == 1
    assert changed == frozenset({"Location"})
    assert snapshot.present_notes(pad.max_sticky_notes)[0]["info"] == "Courtyard"
    _, changed_again = pad.consume_responder_snapshot()
    assert changed_again == frozenset()


@pytest.mark.parametrize("with_contract", [False, True])
def test_freeform_topics_stage_commit_and_round_trip(pad: Notepad, with_contract: bool) -> None:
    if not with_contract:
        pad.theater.read_planning_schema.return_value = None
        pad.configure_schema(None)
    recorder = StagedStoryNotes(pad, "recorder")
    planner = StagedStoryNotes(pad, "planner")
    source = pad.snapshot()
    contract = json.loads(recorder.check_schema("Travel Log"))
    assert contract["type"] == "string"
    assert contract["ownership"] == "shared"
    assert recorder.update_sticky_note("Travel Log", "Found a key | Entered the courtyard").endswith("staged.")
    assert pad.snapshot() == source
    pad.publish_role_updates("recorder", recorder.updates, source=source)
    assert planner.update_sticky_note("Travel Log", "Found a key | Guard may investigate").endswith("staged.")
    pad.publish_role_updates("planner", planner.updates, source=pad.snapshot())
    assert pad.snapshot().notes["Travel Log"] == "Found a key | Guard may investigate"
    assert "Travel Log" not in pad.snapshot().structured_notes

    snapshot = pad.snapshot()
    state = pad.export_state()
    imported = Notepad(pad.theater, canvas_manager=Mock(spec=CanvasStateManager))
    imported.import_state(state)
    assert imported.snapshot() == snapshot
    recovered = Notepad(pad.theater, canvas_manager=Mock(spec=CanvasStateManager))
    recovered.restore_recorded_snapshot(snapshot)
    assert recovered.snapshot() == snapshot


def test_freeform_direct_role_tools_allow_shared_updates(pad: Notepad) -> None:
    assert pad.for_role("recorder").update_sticky_note("Discovery", "Found key").startswith("Added")
    assert pad.for_role("planner").update_sticky_note("Discovery", "The key may open the gate").startswith("Updated")
    assert pad.snapshot().notes["Discovery"] == "The key may open the gate"


@pytest.mark.parametrize("topic,info", [
    ("", "Text"), (" " * 2, "Text"), ("x" * 101, "Text"), ("New", ""), ("New", "x" * 501),
])
def test_freeform_invalid_batch_leaves_all_notes_untouched(pad: Notepad, topic: str, info: str) -> None:
    before = pad.snapshot()
    assert StagedStoryNotes(pad, "recorder").update_sticky_note(topic, info).startswith("Error:")
    with pytest.raises(ValueError):
        pad.publish_role_updates("recorder", {"Valid": "Text", topic: info}, source=before)
    assert pad.snapshot() == before


def test_freeform_dividers_are_checked_only_when_updating_existing_notes(pad: Notepad) -> None:
    pad.publish_role_updates("recorder", {"New": "One | Two"}, source=pad.snapshot())
    before = pad.snapshot()
    with pytest.raises(ValueError, match="divider count"):
        pad.publish_role_updates("planner", {"New": "Only one"}, source=before)
    assert pad.snapshot() == before


def test_freeform_batch_eviction_is_ordered_and_keeps_contract_topics(pad: Notepad) -> None:
    pad.publish_role_updates("recorder", {"One": "First", "Two": "Second"}, source=pad.snapshot())
    source = pad.snapshot()
    persist = Mock()
    pad.publish_role_updates("planner", {"Three": "Third", "Four": "Fourth"}, source=source, persist=persist)
    assert list(pad.snapshot().notes) == ["Location", "Stats", "Ambush", "Three", "Four"]
    assert persist.call_args.args[0] == pad.snapshot()
    assert pad.snapshot().structured_notes == source.structured_notes


def test_freeform_creation_has_room_when_contract_fills_default_limit(pad: Notepad) -> None:
    pad.configure_schema({f"Required {index}": {"owner": "recorder", "initial": "Pinned"} for index in range(5)})
    pad.publish_role_updates("planner", {"New": "Plan"}, source=pad.snapshot())
    assert len(pad.snapshot().notes) == 6
    pad.publish_role_updates("planner", {"Next": "Next plan"}, source=pad.snapshot())
    assert len(pad.snapshot().notes) == 6
    assert "New" not in pad.snapshot().notes
    assert pad.snapshot().notes["Next"] == "Next plan"


def test_freeform_persistence_failure_is_atomic_and_stale_publication_is_rejected(pad: Notepad) -> None:
    before = pad.snapshot()
    with pytest.raises(OSError, match="offline"):
        pad.publish_role_updates("recorder", {"New": "Fact"}, source=before, persist=Mock(side_effect=OSError("offline")))
    assert pad.snapshot() == before
    pad.publish_role_updates("planner", {"Plan": "Wait"}, source=before)
    with pytest.raises(StaleNoteSnapshot):
        pad.publish_role_updates("recorder", {"New": "Fact"}, source=before)
    latest = pad.snapshot()
    pad.restore_recorded_snapshot(before)
    assert pad.snapshot().notes == latest.notes
