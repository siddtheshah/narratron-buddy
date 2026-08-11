import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from components.theater_manager import TheaterManager
from testing.base import BaseTestCase
from tools.music_tool import MusicTools

class TestMusicTools(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.theater_manager = TheaterManager(base_theaters_dir=self.temp_dir)
        self.config = {
            "music": {
                "playlists_folder": self.temp_dir,
                "cooldown_duration": 15.0,
            }
        }
        
        self.ambient_dir = os.path.join(self.temp_dir, "ambient")
        self.combat_dir = os.path.join(self.temp_dir, "combat")
        os.makedirs(self.ambient_dir, exist_ok=True)
        os.makedirs(self.combat_dir, exist_ok=True)

        with open(os.path.join(self.ambient_dir, "description.txt"), "w", encoding="utf-8") as f:
            f.write("Calm ambient soundtrack.")
        with open(os.path.join(self.ambient_dir, "track1.mp3"), "w") as f:
            f.write("dummy audio")
        with open(os.path.join(self.ambient_dir, "track2.mp3"), "w") as f:
            f.write("dummy audio")

        with open(os.path.join(self.combat_dir, "battle.mp3"), "w") as f:
            f.write("dummy audio")

        self.music_tools = MusicTools(self.config, theater_id="test_theater", theater_manager=self.theater_manager)
        self.music_tools.playlists_folder = self.temp_dir

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_playlists_context(self):
        res = self.music_tools.get_playlists_context()
        self.assertIn("ambient", res)
        self.assertIn("combat", res)
        self.assertIn("Calm ambient soundtrack.", res)
        self.assertIn("track1.mp3", res)

    def test_play_playlist_success(self):
        mock_play_cb = MagicMock()
        self.music_tools.on_play_playlist = mock_play_cb

        res = self.music_tools.play_playlist("ambient")
        self.assertIn("Successfully started playing", res)
        mock_play_cb.assert_called_once_with(
            "ambient",
            ["/theaters/test_theater/playlists/ambient/track1.mp3", "/theaters/test_theater/playlists/ambient/track2.mp3"]
        )

    def test_play_playlist_not_found(self):
        res = self.music_tools.play_playlist("unknown")
        self.assertIn("Error: Playlist 'unknown' not found.", res)

    def test_pause_and_resume_playlist(self):
        mock_pause_cb = MagicMock()
        mock_resume_cb = MagicMock()
        self.music_tools.on_pause_playlist = mock_pause_cb
        self.music_tools.on_resume_playlist = mock_resume_cb

        pause_res = self.music_tools.pause_playlist()
        self.assertIn("Successfully paused", pause_res)
        mock_pause_cb.assert_called_once()

        resume_res = self.music_tools.resume_playlist()
        self.assertIn("Successfully resumed", resume_res)
        mock_resume_cb.assert_called_once()

    def test_play_playlist_cooldown(self):
        self.music_tools.cooldown_duration = 5.0
        res1 = self.music_tools.play_playlist("ambient")
        self.assertIn("Successfully started playing", res1)

        res2 = self.music_tools.play_playlist("combat")
        self.assertIn("play_playlist is on cooldown", res2)

    def test_cooldown_expired_callbacks(self):
        self.music_tools.cooldown_duration = 0.1
        on_cooldown_expired = MagicMock()
        self.music_tools.on_cooldown_expired = on_cooldown_expired

        res1 = self.music_tools.play_playlist("ambient")
        self.assertIn("Successfully started playing", res1)

        time.sleep(0.25)
        on_cooldown_expired.assert_called_with("play_playlist")

if __name__ == "__main__":
    unittest.main()

