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


if __name__ == "__main__":
    unittest.main()
