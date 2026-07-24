import os
import tempfile
import unittest

from components.canvas_state import CanvasStateManager

class TestCanvasStateManager(unittest.TestCase):
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

if __name__ == "__main__":
    unittest.main()


