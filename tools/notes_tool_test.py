import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from components.theater_manager import TheaterManager
from testing.base import BaseTestCase
from tools.notes_tool import NotesTools


class TestNotesTools(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.theater_manager = TheaterManager(base_theaters_dir=self.temp_dir)
        self.config = {}
        self.theater_id = "test_theater_123"
        self.notes_tools = NotesTools(self.config, theater_id=self.theater_id, theater_manager=self.theater_manager)
        # Override notes_dir to use isolated temp directory for file operations tests
        self.notes_tools.notes_dir = os.path.join(self.temp_dir, "notes")
        os.makedirs(self.notes_tools.notes_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init_theater_paths(self):
        notes_tools = NotesTools(self.config, theater_id="sess_abc", theater_manager=self.theater_manager)
        self.assertEqual(notes_tools.active_theater_id, "sess_abc")
        self.assertEqual(notes_tools.notes_dir, str(self.theater_manager.theater("sess_abc").notes_artifacts_dir()))

    def test_edit_notes_create_and_overwrite(self):
        res1 = self.notes_tools.edit_notes("quest_log", "Find the magic lamp.")
        self.assertIn("Successfully saved note 'quest_log.txt'", res1)

        notes_dir = self.notes_tools.notes_dir
        note_file = os.path.join(notes_dir, "quest_log.txt")
        self.assertTrue(os.path.exists(note_file))
        with open(note_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "Find the magic lamp.")

        res2 = self.notes_tools.edit_notes("quest_log.txt", "Updated quest log.")
        self.assertIn("Successfully saved note 'quest_log.txt'", res2)
        with open(note_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "Updated quest log.")

    def test_edit_notes_directory_traversal_sanitization(self):
        res = self.notes_tools.edit_notes("../../etc/secret", "malicious content")
        self.assertIn("Successfully saved note 'secret.txt'", res)
        notes_dir = self.notes_tools.notes_dir
        self.assertTrue(os.path.exists(os.path.join(notes_dir, "secret.txt")))

    def test_edit_notes_marks_recent_canvas_activity(self):
        canvas_state_service = MagicMock()
        self.notes_tools.canvas_state_service = canvas_state_service

        self.notes_tools.edit_notes("story", "The dragon woke.")

        canvas_state_service.set_tool_activity.assert_called_once_with(
            "notes", theater_id=self.theater_id, recent_seconds=5.0
        )

    def test_delete_notes(self):
        self.notes_tools.edit_notes("npc_list", "Merchant, Guard")
        notes_dir = self.notes_tools.notes_dir
        note_file = os.path.join(notes_dir, "npc_list.txt")
        self.assertTrue(os.path.exists(note_file))

        del_res = self.notes_tools.delete_notes("npc_list")
        self.assertIn("Successfully deleted note 'npc_list.txt'", del_res)
        self.assertFalse(os.path.exists(note_file))

        del_fail = self.notes_tools.delete_notes("npc_list")
        self.assertIn("Error: Note 'npc_list.txt' not found.", del_fail)


if __name__ == "__main__":
    unittest.main()
