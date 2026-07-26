import os
import shutil
import tempfile
import unittest
from pathlib import Path

from testing.base import BaseTestCase
from components.canvas_state import CanvasStateManager

class TestCanvasStateManager(BaseTestCase):

    def test_canvas_state_manager(self):
        manager = CanvasStateManager(session_id="test_session")
        manager.update_current_playlist("test_playlist", ["/playlists/test/1.mp3"])
        self.assertEqual(manager.current_playlist, "test_playlist")
        self.assertFalse(manager.music_paused)

        manager.pause_current_playlist()
        self.assertTrue(manager.music_paused)

        manager.current_image_basename = "test.jpg"
        manager.add_chat_message("Hello from test", author="user")
        msgs = manager.chat_manager.get_messages()
        self.assertEqual(msgs[-1]["text"], "Hello from test")

    def test_update_shown_image_empty_folder(self):
        with tempfile.TemporaryDirectory() as empty_dir:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp_file:
                tmp_file.write(b"fake image data")
                tmp_path = tmp_file.name

            try:
                manager = CanvasStateManager(session_id="test_session")
                manager.update_shown_image(tmp_path)

                state = manager.get_latest_state()
                self.assertIsNotNone(state["latest"])
                self.assertIn(os.path.basename(tmp_path), state["latest"])
                self.assertGreater(state["time"], 0)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

    def test_doodles_enabled_state_and_persistence(self):
        manager = CanvasStateManager(session_id="test_doodles_session")
        self.assertTrue(manager.doodles_enabled)

        latest_state = manager.get_latest_state()
        self.assertTrue(latest_state.get("doodles_enabled"))

        manager.set_doodles_enabled(False)
        self.assertFalse(manager.doodles_enabled)

        latest_state = manager.get_latest_state()
        self.assertFalse(latest_state.get("doodles_enabled"))

        exported_state, _ = manager.export_session_data()
        self.assertFalse(exported_state["canvas_state"]["doodles_enabled"])

    def test_shown_images_history_capping(self):
        manager = CanvasStateManager(session_id="test_history_session")
        # Add 120 images
        for i in range(120):
            fake_path = f"/path/to/image_{i}.png"
            manager.update_shown_image(fake_path)

        self.assertEqual(len(manager.shown_images_history), 100)
        # Verify the oldest entries (0-19) rolled off, and items 20-119 remain
        last_entry = manager.shown_images_history[-1]
        first_entry = manager.shown_images_history[0]
        self.assertIn("image_119.png", last_entry["path"])
        self.assertIn("image_20.png", first_entry["path"])

    def test_get_latest_state_returns_history(self):
        manager = CanvasStateManager(session_id="test_history_payload")
        manager.update_shown_image("/path/to/scene1.png")
        manager.update_shown_image("/path/to/scene2.png")

        state = manager.get_latest_state()
        self.assertIn("history", state)
        self.assertEqual(len(state["history"]), 2)
        self.assertEqual(state["history"][0]["path"], "/path/to/scene1.png")
        self.assertEqual(state["history"][1]["path"], "/path/to/scene2.png")

if __name__ == "__main__":
    unittest.main()


