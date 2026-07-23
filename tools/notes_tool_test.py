import os
import shutil
import tempfile
import unittest

from tools.notes_tool import NotesTools

class TestNotesTools(unittest.TestCase):
    def setUp(self):
        self.config = {}
        self.notes_tools = NotesTools(self.config)
        self.temp_notes_dir = tempfile.mkdtemp()
        self.notes_tools.notes_dir = self.temp_notes_dir

    def tearDown(self):
        shutil.rmtree(self.temp_notes_dir, ignore_errors=True)

    def test_edit_notes_create_and_overwrite(self):
        res1 = self.notes_tools.edit_notes("quest_log", "Find the magic lamp.")
        self.assertIn("Successfully saved note 'quest_log.txt'", res1)

        note_file = os.path.join(self.temp_notes_dir, "quest_log.txt")
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
        self.assertTrue(os.path.exists(os.path.join(self.temp_notes_dir, "secret.txt")))

    def test_delete_notes(self):
        self.notes_tools.edit_notes("npc_list", "Merchant, Guard")
        note_file = os.path.join(self.temp_notes_dir, "npc_list.txt")
        self.assertTrue(os.path.exists(note_file))

        del_res = self.notes_tools.delete_notes("npc_list")
        self.assertIn("Successfully deleted note 'npc_list.txt'", del_res)
        self.assertFalse(os.path.exists(note_file))

        del_fail = self.notes_tools.delete_notes("npc_list")
        self.assertIn("Error: Note 'npc_list.txt' not found.", del_fail)

if __name__ == "__main__":
    unittest.main()
