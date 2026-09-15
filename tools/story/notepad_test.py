"""Unit tests for the sticky-note state holder."""

from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock

from components.canvas.story_state import StoryState
from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from tools.story.notepad import Notepad


class TestNotepad(unittest.TestCase):
    def setUp(self) -> None:
        self.theater = MagicMock(spec=Theater)
        self.theater.directory.return_value = Path.cwd()
        self.canvas_manager = Mock(spec=CanvasStateManager)
        self.canvas_manager.story = StoryState()

    def _make_notepad(
        self,
        config: dict,
        schema: dict | None = None,
        canvas_manager: CanvasStateManager | None = None,
        **kwargs: object,
    ) -> Notepad:
        self.theater.config.return_value = config
        self.theater.read_planning_schema.return_value = schema
        return Notepad(
            self.theater,
            canvas_manager=canvas_manager or self.canvas_manager,
            **kwargs,
        )

    def test_initializes_required_notes_and_exports_every_note(self) -> None:
        pad = self._make_notepad(
            {
                "max_sticky_notes": 3,
                "required_stickies": {"HUD": "HP: 10"},
                "initial_sticky_notes": {
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

    def test_requires_theater_and_canvas_manager(self) -> None:
        with self.assertRaisesRegex(ValueError, "theater is required"):
            Notepad(None, canvas_manager=self.canvas_manager)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "canvas_manager is required"):
            Notepad(self.theater, canvas_manager=None)  # type: ignore[arg-type]

    def test_updates_validate_dividers_evict_oldest_optional_note_and_notify(self) -> None:
        changed = Mock()
        pad = self._make_notepad(
            {
                "max_sticky_notes": 2,
                "required_stickies": ["HUD"],
                "initial_sticky_notes": {"HUD": "HP: 10 | MP: 5", "Quest": "Find key"},
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
                "max_sticky_notes": 2,
                "required_stickies": {"HUD": "HP: 10"},
                "initial_sticky_notes": {"HUD": "HP: 10", "Quest": "Find key"},
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
                    "fields": {"hp": "Hit points", "mp": "Magic points"},
                    "initial": {"hp": "10", "mp": "5"},
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
                "fields": {"hp": "Hit points", "mp": "Magic points"},
                "initial": {"hp": "10", "mp": "5"},
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
                "initial_sticky_notes": {"Location": "Observatory", "Secret": "Hidden passage"},
            },
            canvas_manager=canvas_manager,
        )

        pad.sync_story_state()
        self.assertEqual(
            canvas_manager.story.get_sticky_notes(),
            [
                {"topic": "Location", "info": "Observatory", "name": "Location", "content": "Observatory"},
                {"topic": "Secret", "info": "Hidden passage", "name": "Secret", "content": "Hidden passage"},
            ],
        )

        pad.update_sticky_note("Mood", "Uneasy")
        self.assertEqual(
            [note["topic"] for note in canvas_manager.story.get_sticky_notes()],
            ["Location", "Secret", "Mood"],
        )

        pad.replace_from_deep_update(
            SimpleNamespace(sticky_notes=[{"topic": "Quest", "info": "Reach the observatory"}])
        )
        self.assertEqual(
            [(note["topic"], note["info"]) for note in canvas_manager.story.get_sticky_notes()],
            [("Quest", "Reach the observatory")],
        )

    def test_recently_changed_tracks_updates_and_is_cleared_on_read(self) -> None:
        pad = self._make_notepad(
            {
                "max_sticky_notes": 3,
                "initial_sticky_notes": {"HUD": "HP: 10"},
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
                "max_sticky_notes": 3,
                "initial_sticky_notes": {"HUD": "HP: 10", "Quest": "Find key"},
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
                "fields": {"name": "Name", "charm": "Charm"},
                "initial": {"name": "Unnamed", "charm": "Novice"},
                "render": "Name: {name} | Charm: {charm}",
            },
            "Affection Tracker": {
                "fields": {"mood": "Mood"},
                "initial": {"mood": "Curious"},
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
        pad = self._make_notepad({"max_sticky_notes": 4}, schema=schema)
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


if __name__ == "__main__":
    unittest.main()
