"""Unit tests for the sticky-note state holder."""

from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from pydantic import JsonValue

from components.canvas.story_state import StoryState
from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from components.notepad import Notepad, parse_planning_schema


class TestNotepad(unittest.TestCase):
    def setUp(self) -> None:
        self.theater = MagicMock(spec=Theater)
        self.theater.directory.return_value = Path.cwd()
        self.canvas_manager = Mock(spec=CanvasStateManager)
        self.canvas_manager.story = StoryState()

    def _make_notepad(
        self,
        config: dict[str, JsonValue],
        schema: dict[str, JsonValue] | None = None,
        canvas_manager: CanvasStateManager | None = None,
        on_change: Mock | None = None,
    ) -> Notepad:
        self.theater.config.return_value = {
            "story_planning": {"adventure_mode": schema is not None, "enforce_structured": config.get("enforce_structured", False)},
            "notepad": config,
        }
        self.theater.read_planning_schema.return_value = schema
        return Notepad(
            self.theater,
            canvas_manager=canvas_manager or self.canvas_manager,
            on_change=on_change,
        )

    def test_initializes_required_notes_and_exports_every_note(self) -> None:
        pad = self._make_notepad(
            {
                "max_stickies": 3,
                "required_stickies": {"HUD": "HP: 10"},
                "initial_stickies": {
                    "HUD": "HP: 10",
                    "Secret": "The gate is trapped",
                    "Quest": "Reach the gate",
                },
            }
        )

        self.assertEqual(pad.get_required_sticky_notes(), ["HUD"])
        self.assertEqual(
            [note["topic"] for note in pad.get_present_sticky_notes()],
            ["HUD", "Secret", "Quest"],
        )
        exported = pad.export_state()
        self.assertEqual([note["topic"] for note in exported["sticky_notes"]], ["HUD", "Secret", "Quest"])
        self.assertEqual([note["topic"] for note in exported["all_sticky_notes"]], ["HUD", "Secret", "Quest"])

    def test_revision_tracks_changed_content_and_eviction_only(self) -> None:
        changed = Mock()
        pad = self._make_notepad(
            {"max_stickies": 1, "initial_stickies": {"HUD": "HP: 10 | MP: 5"}},
            on_change=changed,
        )
        self.assertEqual(pad.revision, 0)
        self.assertIn("Unchanged", pad.update_sticky_note("HUD", "HP: 10 | MP: 5"))
        self.assertIn("Error", pad.update_sticky_note("HUD", "HP: 9"))
        self.assertEqual(pad.revision, 0)
        self.assertEqual(pad.get_recently_changed_topics(), frozenset())
        changed.assert_not_called()

        pad.update_sticky_note("HUD", "HP: 9 | MP: 5")
        self.assertEqual(pad.revision, 1)
        pad.update_sticky_note("Quest", "Find the gate")
        self.assertEqual(pad.revision, 2)
        self.assertEqual(changed.call_count, 2)
        self.assertEqual(pad.export_state()["notepad_revision"], 2)
        self.assertEqual([note["topic"] for note in pad.get_present_sticky_notes()], ["Quest"])

    def test_revision_tracks_structured_fields_even_when_rendering_is_unchanged(self) -> None:
        pad = self._make_notepad(
            {"enforce_structured": True},
            {"Stats": {
                "fields": {
                    "hp": {"description": "Hit points", "initial": "10"},
                    "secret": {"description": "Hidden score", "initial": "0"},
                },
                "render": "HP: {hp}",
            }},
        )
        pad.update_sticky_note("Stats", '{"hp":"10","secret":"1"}')
        self.assertEqual(pad.revision, 1)
        self.assertEqual(pad.get_present_sticky_notes()[0]["info"], "HP: 10")
        pad.update_sticky_note("Stats", '{"secret":"1","hp":"10"}')
        self.assertEqual(pad.revision, 1)
        self.assertIn("Error", pad.update_sticky_note("Stats", '{"hp":"9"}'))
        self.assertEqual(pad.revision, 1)

    def test_nested_fields_initialize_and_render_in_declaration_order(self) -> None:
        pad = self._make_notepad(
            {"enforce_structured": True},
            {"Stats": {"fields": {
                "hp": {"description": "Hit points", "initial": "10"},
                "mp": {"description": "Magic points", "initial": "5"},
                "condition": {"description": "Current condition"},
            }}},
        )
        self.assertEqual(pad.get_present_sticky_notes()[0]["info"], "hp: 10 | mp: 5 | condition: undefined")
        self.assertEqual(
            pad.get_present_structured_sticky_notes(),
            {"Stats": {"hp": "10", "mp": "5", "condition": "undefined"}},
        )
        schema = pad.deep_plan_update_model.model_json_schema()
        self.assertEqual(schema["$defs"]["Sticky_Stats"]["properties"]["hp"]["description"], "Hit points")
        pad.update_sticky_note("Stats", '{"mp":"4","condition":"Healthy","hp":"9"}')
        self.assertEqual(pad.get_present_sticky_notes()[0]["info"], "hp: 9 | mp: 4 | condition: Healthy")

    def test_initial_defaults_preserve_explicit_values_for_plain_and_structured_notes(self) -> None:
        pad = self._make_notepad(
            {"enforce_structured": True},
            {
                "Unknown": {"owner": "recorder", "description": "Not established yet"},
                "Empty": {"owner": "recorder", "initial": ""},
                "Known": {"owner": "recorder", "initial": "Established"},
                "Stats": {"owner": "recorder", "fields": {
                    "unknown": {"description": "Not established yet"},
                    "empty": {"description": "Intentionally empty", "initial": ""},
                    "known": {"description": "Established fact", "initial": "Established"},
                }},
            },
        )
        self.assertEqual(pad.get_present_structured_sticky_notes(), {
            "Unknown": "undefined",
            "Empty": "",
            "Known": "Established",
            "Stats": {"unknown": "undefined", "empty": "", "known": "Established"},
        })
        self.assertEqual(pad.snapshot().notes["Unknown"], "undefined")
        self.assertEqual(pad.snapshot().notes["Stats"], "unknown: undefined | known: Established")

    def test_revision_round_trip_and_legacy_migration(self) -> None:
        pad = self._make_notepad({})
        pad.import_state({
            "deep_plan": {"revision": 7, "sticky_notes": [{"topic": "Quest", "info": "Find key"}]},
        })
        self.assertEqual(pad.revision, 7)
        self.assertEqual(pad.get_present_sticky_notes()[0]["info"], "Find key")
        pad.update_sticky_note("Quest", "Gate opened")
        exported = pad.export_state()
        self.assertEqual(exported["notepad_revision"], 8)
        self.assertNotIn("deep_plan", exported)
        self.assertNotIn("deep_plan_revision", exported)

        restored = self._make_notepad({})
        restored.import_state(exported)
        self.assertEqual(restored.revision, 8)
        restored.update_sticky_note("Quest", "Enter gate")
        self.assertEqual(restored.revision, 9)

    def test_import_prefers_current_notes_and_revision_over_stale_legacy_plan(self) -> None:
        pad = self._make_notepad({})
        pad.import_state({
            "notepad_revision": 4,
            "sticky_notes": [{"topic": "Quest", "info": "Gate opened"}],
            "deep_plan_revision": 3,
            "deep_plan": {"revision": 3, "sticky_notes": [{"topic": "Quest", "info": "Find key"}]},
        })
        self.assertEqual(pad.revision, 4)
        self.assertEqual(pad.get_present_sticky_notes()[0]["info"], "Gate opened")
        for revision in (None, "invalid", -1):
            with self.subTest(revision=revision):
                pad.import_state({"notepad_revision": revision})
                self.assertEqual(pad.revision, 0)

    def test_schema_reset_advances_revision_when_note_contents_change(self) -> None:
        pad = self._make_notepad({}, schema={})
        schema = {"Location": {"initial": "Observatory"}}
        pad.configure_schema(schema)
        self.assertEqual(pad.revision, 1)
        pad.configure_schema(schema)
        self.assertEqual(pad.revision, 1)

    def test_default_reference_stickies_are_retained(self) -> None:
        from utils.config_loader import get_theater_default_config

        config = get_theater_default_config()["notepad"]
        pad = self._make_notepad(config)
        topics = ["Current Scene (Ref)", "Current Present Characters (Refs)"]
        self.assertEqual(pad.get_required_sticky_notes(), topics)
        self.assertEqual([note["topic"] for note in pad.get_present_sticky_notes()], topics)
        pad.update_sticky_note(topics[0], "library_scene")
        pad.update_sticky_note(topics[1], "<Arthur Modella>")
        for index in range(pad.max_sticky_notes + 1):
            pad.update_sticky_note(f"Note {index}", "Optional detail")
        notes = {note["topic"]: note["info"] for note in pad.get_present_sticky_notes()}
        self.assertEqual(notes[topics[0]], "library_scene")
        self.assertEqual(notes[topics[1]], "<Arthur Modella>")

    def test_requires_theater_and_canvas_manager(self) -> None:
        with self.assertRaisesRegex(ValueError, "theater is required"):
            Notepad(None, canvas_manager=self.canvas_manager)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "canvas_manager is required"):
            Notepad(self.theater, canvas_manager=None)  # type: ignore[arg-type]

    def test_updates_validate_dividers_evict_oldest_optional_note_and_notify(self) -> None:
        changed = Mock()
        pad = self._make_notepad(
            {
                "max_stickies": 2,
                "required_stickies": ["HUD"],
                "initial_stickies": {"HUD": "HP: 10 | MP: 5", "Quest": "Find key"},
            },
            on_change=changed,
        )

        self.assertIn("divider count mismatch", pad.update_sticky_note("HUD", "HP: 9"))
        changed.assert_not_called()

        self.assertIn("Updated sticky note 'HUD'", pad.update_sticky_note("HUD", "HP: 9 | MP: 5"))
        self.assertIn("Oldest sticky note 'Quest' was dropped", pad.update_sticky_note("Clue", "Brass key"))
        self.assertEqual([note["topic"] for note in pad.get_present_sticky_notes()], ["HUD", "Clue"])
        self.assertEqual(changed.call_count, 2)

    def test_deep_replacement_preserves_required_notes_and_applies_limit(self) -> None:
        pad = self._make_notepad(
            {
                "max_stickies": 2,
                "required_stickies": {"HUD": "HP: 10"},
                "initial_stickies": {"HUD": "HP: 10", "Quest": "Find key"},
            }
        )

        pad.replace_from_deep_update(
            SimpleNamespace(
                sticky_notes=[
                    {"topic": "Quest", "info": "Gate opened"},
                    {"topic": "Clue", "info": "Brass key"},
                ]
            )
        )

        self.assertEqual(
            [(note["topic"], note["info"]) for note in pad.get_present_sticky_notes()],
            [("Clue", "Brass key"), ("HUD", "HP: 10")],
        )

    def test_structured_notes_round_trip_through_export_and_import(self) -> None:
        schema = {
                "Stats": {
                    "required": True,
                    "fields": {
                        "hp": {"description": "Hit points", "initial": "10"},
                        "mp": {"description": "Magic points", "initial": "5"},
                    },
                    "render": "HP: {hp} | MP: {mp}",
                },
                "Secret": {"required": False, "initial": "The gate is trapped"},
        }
        pad = self._make_notepad({}, schema)
        update_model = pad.deep_plan_update_model

        update = update_model.model_validate(
            {"sticky_notes": {"Stats": {"hp": "8", "mp": "3"}, "Secret": "Disarmed"}}
        )
        pad.replace_from_deep_update(update)
        state = pad.export_state()

        self.assertEqual(pad.get_present_structured_sticky_notes()["Stats"], {"hp": "8", "mp": "3"})
        self.assertEqual(
            state["sticky_notes"],
            [{"topic": "Stats", "info": "HP: 8 | MP: 3"}, {"topic": "Secret", "info": "Disarmed"}],
        )

        reloaded = self._make_notepad({}, schema)
        reloaded.import_state(state)
        self.assertEqual(reloaded.get_present_sticky_notes()[0]["info"], "HP: 8 | MP: 3")
        self.assertEqual(reloaded.get_present_structured_sticky_notes()["Secret"], "Disarmed")

    def test_enforced_structured_updates_expose_schema_and_preserve_other_notes(self) -> None:
        schema = {
            "Stats": {
                "fields": {
                    "hp": {"description": "Hit points", "initial": "10"},
                    "mp": {"description": "Magic points", "initial": "5"},
                },
                "render": "HP: {hp} | MP: {mp}",
            },
            "Location": {"initial": "Observatory"},
        }
        pad = self._make_notepad(
            {"enforce_structured": True},
            schema,
        )

        self.assertIn("requires a JSON object", pad.update_sticky_note("Stats", "HP: 9 | MP: 4"))
        self.assertIn('"required": ["hp", "mp"]', pad.check_schema("Stats"))
        self.assertIn(
            "Updated sticky note 'Stats'",
            pad.update_sticky_note("Stats", '{"hp": "9", "mp": "4"}'),
        )
        self.assertEqual(
            pad.get_present_structured_sticky_notes(),
            {"Stats": {"hp": "9", "mp": "4"}, "Location": "Observatory"},
        )
        self.assertIn("not configured", pad.update_sticky_note("Untracked", "value"))

    def test_syncs_canvas_story_state_from_the_internal_pad(self) -> None:
        canvas_manager = Mock(spec=CanvasStateManager)
        canvas_manager.story = StoryState()
        pad = self._make_notepad(
            {
                "initial_stickies": {"Location": "Observatory", "Secret": "Hidden passage"},
            },
            canvas_manager=canvas_manager,
        )

        self.assertEqual(
            pad.get_present_sticky_notes(),
            [
                {"topic": "Location", "info": "Observatory", "name": "Location", "content": "Observatory"},
                {"topic": "Secret", "info": "Hidden passage", "name": "Secret", "content": "Hidden passage"},
            ],
        )

        pad.update_sticky_note("Mood", "Uneasy")
        self.assertEqual(
            [note["topic"] for note in pad.get_present_sticky_notes()],
            ["Location", "Secret", "Mood"],
        )

        pad.replace_from_deep_update(
            SimpleNamespace(sticky_notes=[{"topic": "Quest", "info": "Reach the observatory"}])
        )
        self.assertEqual(
            [(note["topic"], note["info"]) for note in pad.get_present_sticky_notes()],
            [("Quest", "Reach the observatory")],
        )

    def test_recently_changed_tracks_updates_and_is_cleared_on_read(self) -> None:
        pad = self._make_notepad(
            {
                "max_stickies": 3,
                "initial_stickies": {"HUD": "HP: 10"},
            }
        )
        # Initial stickies should NOT appear in recently-changed.
        self.assertEqual(pad.get_recently_changed_topics(), frozenset())

        pad.update_sticky_note("HUD", "HP: 9")
        pad.update_sticky_note("Quest", "Reach the gate")
        changed = pad.get_recently_changed_topics()
        self.assertIn("HUD", changed)
        self.assertIn("Quest", changed)

        pad.mark_stickies_read()
        self.assertEqual(pad.get_recently_changed_topics(), frozenset())

    def test_recently_changed_tracks_deep_update_only_for_modified_values(self) -> None:
        pad = self._make_notepad(
            {
                "max_stickies": 3,
                "initial_stickies": {"HUD": "HP: 10", "Quest": "Find key"},
            }
        )
        pad.mark_stickies_read()  # clear any initial pollution

        from types import SimpleNamespace

        pad.replace_from_deep_update(
            SimpleNamespace(
                sticky_notes=[
                    {"topic": "HUD", "info": "HP: 10"},   # unchanged
                    {"topic": "Quest", "info": "Gate opened"},  # changed
                ]
            )
        )
        changed = pad.get_recently_changed_topics()
        self.assertNotIn("HUD", changed)
        self.assertIn("Quest", changed)

    def test_schema_fields_are_assumed_required_and_pinned_in_order(self) -> None:
        schema = {
            "Player Character": {
                "fields": {
                    "name": {"description": "Name", "initial": "Unnamed"},
                    "charm": {"description": "Charm", "initial": "Novice"},
                },
                "render": "Name: {name} | Charm: {charm}",
            },
            "Affection Tracker": {
                "fields": {
                    "mood": {"description": "Mood", "initial": "Curious"},
                },
                "render": "Mood: {mood}",
            },
            "Activity Log": {
                "initial": "Session started",
            },
        }
        pad = self._make_notepad({"enforce_structured": True}, schema=schema)

        # All fields are assumed to be required in schema definition order
        self.assertEqual(
            pad.get_required_sticky_notes(),
            ["Player Character", "Affection Tracker", "Activity Log"],
        )

        # Initial order is pinned
        self.assertEqual(
            [note["topic"] for note in pad.get_present_sticky_notes()],
            ["Player Character", "Affection Tracker", "Activity Log"],
        )

        # Updating an earlier sticky note does NOT move it to the end; order remains pinned
        pad.update_sticky_note("Player Character", '{"name": "Hero", "charm": "High"}')
        self.assertEqual(
            [note["topic"] for note in pad.get_present_sticky_notes()],
            ["Player Character", "Affection Tracker", "Activity Log"],
        )
        self.assertEqual(
            pad.get_present_sticky_notes()[0]["info"],
            "Name: Hero | Charm: High",
        )

        # Updating another sticky maintains the exact pinned order
        pad.update_sticky_note("Affection Tracker", '{"mood": "Enamored"}')
        self.assertEqual(
            [note["topic"] for note in pad.get_present_sticky_notes()],
            ["Player Character", "Affection Tracker", "Activity Log"],
        )

        # Deep update maintains pinned order
        update_model = pad.deep_plan_update_model
        update = update_model.model_validate(
            {
                "sticky_notes": {
                    "Activity Log": "Entered temple",
                    "Player Character": {"name": "Hero", "charm": "Master"},
                    "Affection Tracker": {"mood": "Devoted"},
                }
            }
        )
        pad.replace_from_deep_update(update)
        self.assertEqual(
            [note["topic"] for note in pad.get_present_sticky_notes()],
            ["Player Character", "Affection Tracker", "Activity Log"],
        )

        # Export and re-import maintains pinned order
        state = pad.export_state()
        reloaded = self._make_notepad({}, schema=schema)
        reloaded.import_state(state)
        self.assertEqual(
            [note["topic"] for note in reloaded.get_present_sticky_notes()],
            ["Player Character", "Affection Tracker", "Activity Log"],
        )

    def test_schema_pinned_order_with_unpinned_dynamic_notes(self) -> None:
        schema = {
            "First": {"initial": "1"},
            "Second": {"initial": "2"},
        }
        pad = self._make_notepad({}, schema=schema)
        pad.max_sticky_notes = 4
        pad.update_sticky_note("Extra1", "info1")
        pad.update_sticky_note("Extra2", "info2")
        self.assertEqual(
            [note["topic"] for note in pad.get_present_sticky_notes()],
            ["First", "Second", "Extra1", "Extra2"],
        )
        # Updating First does not unpin it
        pad.update_sticky_note("First", "new1")
        self.assertEqual(
            [note["topic"] for note in pad.get_present_sticky_notes()],
            ["First", "Second", "Extra1", "Extra2"],
        )
        # Updating Extra1 moves Extra1 to the end among unpinned notes
        pad.update_sticky_note("Extra1", "info1_updated")
        self.assertEqual(
            [note["topic"] for note in pad.get_present_sticky_notes()],
            ["First", "Second", "Extra2", "Extra1"],
        )


@pytest.mark.parametrize("adventure_settings", [{}, {"adventure_mode": False}])
@pytest.mark.parametrize("enforce_structured", [None, False, True])
def test_normal_narration_uses_only_notepad_settings(
    tmp_path: Path,
    adventure_settings: dict[str, bool],
    enforce_structured: bool | None,
) -> None:
    theater = Mock(spec=Theater)
    theater.config.return_value = {
        "notepad": {
            "max_stickies": 2,
            "initial_stickies": {"Scene": "Gate", "Clue": "Key"},
            "required_stickies": ["Scene"],
            "hidden_stickies": ["Scene"],
        },
        "story_planning": {
            **adventure_settings,
            "enforce_structured": True,
            "initial_elements": {"Legacy": "Ignored"},
            "required_stickies": ["Legacy"],
            "hidden_stickies": ["Legacy"],
            "max_named_elements": 1,
            "stickies": {"Inline": {"initial": "Ignored"}},
        },
    }
    theater.directory.return_value = tmp_path
    theater.read_planning_schema.side_effect = AssertionError("Normal narration must not read planning.yaml")
    with patch(
        "components.notepad.parse_planning_schema",
        side_effect=AssertionError("Normal narration must not parse a planning schema"),
    ) as parse_schema:
        pad = Notepad(
            theater,
            canvas_manager=Mock(spec=CanvasStateManager),
            enforce_structured=enforce_structured,
        )
        pad.configure_schema({
            "Player Info": {
                "fields": {"origin": "Legacy field description"},
                "initial": {"origin": "Pilot"},
            },
        })
        parse_schema.assert_not_called()

    assert pad.enforce_structured is False
    assert pad.max_stickies == 2
    assert pad.get_required_sticky_notes() == ["Scene"]
    assert pad.get_hidden_sticky_notes() == ["Scene"]
    assert "Clue" in pad.update_sticky_note("Quest", "Find the lock")
    assert [(note["topic"], note["info"]) for note in pad.get_present_sticky_notes()] == [
        ("Scene", "Gate"), ("Quest", "Find the lock"),
    ]


@pytest.mark.parametrize("schema", [None, {}, {"stickies": {}}])
def test_structured_adventure_requires_a_planning_contract(schema: dict[str, JsonValue] | None) -> None:
    theater = Mock(spec=Theater)
    theater.config.return_value = {
        "story_planning": {
            "adventure_mode": True,
            "stickies": {"Inline": {"initial": "Not a file contract"}},
        },
    }
    theater.read_planning_schema.return_value = schema
    with pytest.raises(ValueError, match="requires a nonempty planning.yaml"):
        Notepad(theater, canvas_manager=Mock(spec=CanvasStateManager))


def test_unstructured_adventure_allows_missing_contract_and_ignores_notepad_settings() -> None:
    theater = Mock(spec=Theater)
    theater.config.return_value = {
        "story_planning": {"adventure_mode": True, "enforce_structured": False},
        "notepad": {
            "initial_stickies": {"Normal": "Ignored"},
            "required_stickies": ["Normal"],
            "hidden_stickies": ["Normal"],
            "max_stickies": 1,
        },
    }
    theater.read_planning_schema.return_value = None
    pad = Notepad(theater, canvas_manager=Mock(spec=CanvasStateManager))
    assert pad.get_present_sticky_notes() == []
    assert pad.get_required_sticky_notes() == []
    assert pad.get_hidden_sticky_notes() == []
    assert pad.update_sticky_note("New", "Free form").startswith("Added")


def test_adventure_reads_contract_visibility_and_retains_all_topics(tmp_path: Path) -> None:
    from components.theater_manager import TheaterManager

    theater = TheaterManager(tmp_path).theater("contract")
    theater.directory().mkdir(parents=True, exist_ok=True)
    (theater.directory() / "theater.yaml").write_text(
        "story_planning:\n  adventure_mode: true\n  enforce_structured: true\nnotepad:\n  max_stickies: 1\n"
        "  required_stickies: [Normal]\n  hidden_stickies: [Visible]\n"
        "  initial_stickies: {Normal: Ignored}\n", encoding="utf-8",
    )
    (theater.directory() / "planning.yaml").write_text(
        "Visible:\n  owner: recorder\n  initial: Public\n"
        "Secret:\n  owner: planner\n  hidden: true\n  initial: Private\n"
        + "".join(f"Topic {index}:\n  owner: recorder\n  initial: 'Value {index}'\n" for index in range(10)),
        encoding="utf-8",
    )
    pad = Notepad(theater, canvas_manager=Mock(spec=CanvasStateManager))
    assert pad.enforce_structured is True
    assert pad.get_hidden_sticky_notes() == ["Secret"]
    assert pad.get_required_sticky_notes() == ["Visible", "Secret", *[f"Topic {index}" for index in range(10)]]
    assert len(pad.get_present_sticky_notes()) == 12
    assert pad.max_stickies == 12
    assert pad.for_role("planner").update_sticky_note("Secret", "Revised plan").startswith("Updated")
    assert pad.get_present_sticky_notes()[1]["info"] == "Revised plan"
    before = pad.export_state()
    with pytest.raises(ValueError, match="requires a nonempty planning.yaml"):
        pad.configure_schema({})
    assert pad.export_state() == before


@pytest.mark.parametrize("entry", [
    {"fields": {"hp": "Hit points"}},
    {"fields": ["hp"]},
    {"fields": {"hp": {"description": "Hit points", "initial": 10}}},
    {"fields": {"hp": {"description": False, "initial": "10"}}},
    {"fields": {"hp": {"description": "Hit points"}}, "initial": {"hp": "10"}},
])
def test_planning_schema_rejects_old_fields_and_non_string_values(entry: dict[str, JsonValue]) -> None:
    with pytest.raises(ValueError):
        parse_planning_schema({"Stats": entry})


if __name__ == "__main__":
    unittest.main()
