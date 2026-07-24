import os
import tempfile
import unittest

from components.canvas_state import CanvasStateManager

class TestCanvasStateManager(unittest.TestCase):
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

    def test_update_shown_image_empty_folder(self):
        with tempfile.TemporaryDirectory() as empty_dir:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_file:
                tmp_file.write(b"fake image data")
                tmp_path = tmp_file.name

            try:
                manager = CanvasStateManager()
                manager.update_shown_image(tmp_path)

                state = manager.get_latest_state(image_folder=empty_dir)
                self.assertIsNotNone(state["latest"])
                self.assertIn(os.path.basename(tmp_path), state["latest"])
                self.assertGreater(state["time"], 0)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

if __name__ == "__main__":
    unittest.main()

