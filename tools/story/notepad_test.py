"""Unit tests for the sticky-note state holder."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from components.canvas.story_state import StoryState
from components.canvas_state import CanvasStateManager
from tools.story.notepad import Notepad
from tools.story.story_planning_module import (
    build_deep_plan_update_model,
    parse_planning_schema,
    render_structured_sticky,
)


class TestNotepad(unittest.TestCase):
    def test_initializes_required_notes_and_exports_every_note(self) -> None:
        pad = Notepad(
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

    def test_updates_validate_dividers_evict_oldest_optional_note_and_notify(self) -> None:
        changed = Mock()
        pad = Notepad(
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
        pad = Notepad(
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
        definitions = parse_planning_schema(
            {
                "Stats": {
                    "required": True,
                    "fields": {"hp": "Hit points", "mp": "Magic points"},
                    "initial": {"hp": "10", "mp": "5"},
                    "render": "HP: {hp} | MP: {mp}",
                },
                "Secret": {"required": False, "initial": "The gate is trapped"},
            }
        )
        update_model = build_deep_plan_update_model(definitions)
        pad = Notepad({})
        pad.configure_schema(definitions, update_model, render_structured_sticky)

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

        reloaded = Notepad({})
        reloaded.configure_schema(definitions, update_model, render_structured_sticky)
        reloaded.import_state(state)
        self.assertEqual(reloaded.get_present_sticky_notes()[0]["info"], "HP: 8 | MP: 3")
        self.assertEqual(reloaded.get_present_structured_sticky_notes()["Secret"], "Disarmed")

    def test_syncs_canvas_story_state_from_the_internal_pad(self) -> None:
        canvas_manager = Mock(spec=CanvasStateManager)
        canvas_manager.story = StoryState()
        pad = Notepad(
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
