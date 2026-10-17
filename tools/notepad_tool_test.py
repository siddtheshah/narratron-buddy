import unittest
from unittest.mock import MagicMock

from components.canvas.story_state import StoryState
from components.canvas_state import CanvasStateManager
from components.notepad import Notepad
from components.theater_manager import Theater
from tools.notepad_tool import NotepadTool


class TestNotepadTool(unittest.TestCase):
    def setUp(self) -> None:
        self.theater = MagicMock(spec=Theater)
        self.theater.theater_id = "notepad-tool"
        self.theater.config.return_value = {
            "story_planning": {
                "adventure_mode": False,
                "initial_sticky_notes": {"Location": "Observatory"},
            }
        }
        self.canvas = MagicMock(spec=CanvasStateManager)
        self.canvas.story = StoryState()
        self.notepad = MagicMock(spec=Notepad)
        self.canvas.notepad = self.notepad

    def test_persists_notes_without_constructing_story_modules(self) -> None:
        self.notepad.get_present_sticky_notes.side_effect = [
            [{"topic": "Location", "info": "Observatory"}],
            [{"topic": "Location", "info": "Observatory"}, {"topic": "Mood", "info": "Uneasy"}],
        ]
        self.notepad.export_state.return_value = {
            "sticky_notes": [{"topic": "Location", "info": "Observatory"}, {"topic": "Mood", "info": "Uneasy"}]
        }

        def fake_update(topic: str, info: str) -> str:
            if self.notepad.on_change is not None:
                self.notepad.on_change()
            return f"Added sticky note '{topic}'"

        self.notepad.update_sticky_note.side_effect = fake_update

        tool = NotepadTool(self.theater, self.canvas)

        self.assertEqual(tool.get_present_sticky_notes()[0]["topic"], "Location")
        self.assertIn("Added sticky note 'Mood'", tool.update_sticky_note("Mood", "Uneasy"))
        self.assertEqual(
            self.canvas.story.get_story_planning_state()["sticky_notes"],
            [{"topic": "Location", "info": "Observatory"}, {"topic": "Mood", "info": "Uneasy"}],
        )

        reloaded = NotepadTool(self.theater, self.canvas)
        self.assertEqual(
            [note["topic"] for note in reloaded.get_present_sticky_notes()],
            ["Location", "Mood"],
        )

    def test_fetches_notepad_from_canvas_manager(self) -> None:
        tool = NotepadTool(self.theater, self.canvas)
        self.assertIs(tool.notepad, self.notepad)


if __name__ == "__main__":
    unittest.main()
