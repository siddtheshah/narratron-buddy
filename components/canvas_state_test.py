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

if __name__ == "__main__":
    unittest.main()
