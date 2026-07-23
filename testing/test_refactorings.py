import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from components.canvas_state import CanvasStateManager
from utils.config_loader import get_config
from utils.image_utils import extract_image_prompt, resolve_image_path

class TestRefactorings(unittest.TestCase):
    def test_config_loader(self):
        config = get_config()
        self.assertIsInstance(config, dict)
        self.assertIn("image_generation", config)

    def test_image_utils_path_resolution(self):
        path = resolve_image_path("non_existent_file.png")
        self.assertIsNone(path)

    def test_canvas_state_manager(self):
        manager = CanvasStateManager()
        manager.update_current_playlist("test_playlist", ["/playlists/test/1.mp3"])
        self.assertEqual(manager.current_playlist, "test_playlist")
        self.assertFalse(manager.music_paused)

        manager.pause_current_playlist()
        self.assertTrue(manager.music_paused)

        manager.resume_current_playlist()
        self.assertFalse(manager.music_paused)

        manager.add_chat_message("Hello from test", author="user")
        msgs = manager.chat_manager.get_messages()
        self.assertEqual(msgs[-1]["text"], "Hello from test")

if __name__ == "__main__":
    unittest.main()
