import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from components.theater_manager import TheaterManager
from providers.music_provider import MusicGenerationResult
from testing.base import BaseTestCase
from services.agent import get_playlists_context
from tools.music_catalog import MusicCatalog
from tools.music_tool import MusicTools


class FakeMusicCatalogDatabase:
    """In-memory stand-in for the PostgreSQL BM25 candidate query."""
    def __init__(self):
        self.entries = []

    def add_music_catalog_track(self, track_id, artifact_filename, prompt, provider, model, term_frequencies):
        self.entries.append({"id": track_id, "filename": artifact_filename, "prompt": prompt})

    def find_music_catalog_candidates(self, term_frequencies, limit):
        return self.entries[:limit]


class TestMusicTools(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.theater_manager = TheaterManager(base_theaters_dir=self.temp_dir)
        self.config = {
            "music": {
                "playlists_folder": self.temp_dir,
                "generation_cooldown": 0.0,
                "switch_cooldown": 0.0,
                "provider": "lyria",
                "use_generated_music": True,
            }
        }
        
        self.ambient_dir = os.path.join(self.temp_dir, "test_theater", "playlists", "ambient")
        self.combat_dir = os.path.join(self.temp_dir, "test_theater", "playlists", "combat")
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

        self.music_tools = self.make_tools(self.config)
        self.music_tools.playlists_folder = os.path.join(self.temp_dir, "test_theater", "playlists")
        self.music_tools.cooldown_duration = 0.0
        self.music_tools._last_call_times.clear()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def make_tools(self, config):
        return MusicTools(
            config,
            theater_id="test_theater",
            theater_manager=self.theater_manager,
            music_catalog=MusicCatalog(self.theater_manager.music_catalog_dir()),
        )

    def test_get_playlists_context(self):
        theater = self.theater_manager.theater("test_theater")
        res = get_playlists_context(theater)
        self.assertIn("ambient", res)
        self.assertIn("combat", res)
        self.assertIn("Calm ambient soundtrack.", res)
        self.assertIn("track1.mp3", res)

    def test_play_music_playlist_success(self):
        mock_play_cb = MagicMock()
        self.music_tools.on_play_music = mock_play_cb

        res = self.music_tools.play_music("ambient")
        self.assertIn("Successfully started playing music", res)
        mock_play_cb.assert_called_once_with(
            "ambient",
            ["/theaters/test_theater/playlists/ambient/track1.mp3", "/theaters/test_theater/playlists/ambient/track2.mp3"]
        )

    def test_play_music_not_found(self):
        res = self.music_tools.play_music("unknown")
        self.assertIn("Error: Music or playlist 'unknown' not found.", res)

    def test_pause_and_resume_music(self):
        mock_pause_cb = MagicMock()
        mock_resume_cb = MagicMock()
        self.music_tools.on_pause_music = mock_pause_cb
        self.music_tools.on_resume_music = mock_resume_cb

        pause_res = self.music_tools.pause_music()
        self.assertIn("Successfully paused", pause_res)
        mock_pause_cb.assert_called_once()

        resume_res = self.music_tools.resume_music()
        self.assertIn("Successfully resumed", resume_res)
        mock_resume_cb.assert_called_once()

    def test_create_music_and_play_handle(self):
        mock_provider = MagicMock()
        mock_provider.generate.return_value = MusicGenerationResult(
            audio_bytes=b"fake audio data",
            mime_type="audio/mp3",
            provider="lyria",
            model="lyria-3-pro-preview",
        )
        self.music_tools._music_provider = mock_provider
        self.music_tools.cooldown_duration = 0.0

        res = self.music_tools.create_music("epic adventure theme", handle="hero_theme")
        self.assertIn("Music generation started in background with handle 'hero_theme'", res)

        self.music_tools.join_generation(timeout=5.0)

        # Check saved file in output/music
        output_dir = self.music_tools.output_dir
        self.assertTrue(os.path.exists(output_dir))
        files = os.listdir(output_dir)
        self.assertTrue(any("hero_theme" in f for f in files))

        # Check playing by handle
        play_res = self.music_tools.play_music("hero_theme")
        self.assertIn("Successfully started playing music 'hero_theme'", play_res)

        catalog_files = [name for name in os.listdir(self.theater_manager.music_catalog_dir()) if name.endswith(".mp3")]
        self.assertEqual(len(catalog_files), 1)

    def test_create_music_reuses_private_catalog_without_provider_call(self):
        # The local TF-IDF pass nominates the candidate; a deterministic
        # reranker avoids a network dependency while testing private copying.
        self.music_tools.music_catalog.database_manager = FakeMusicCatalogDatabase()
        catalog_source = Path(self.temp_dir) / "seed.mp3"
        catalog_source.write_bytes(b"catalog audio")
        entry = self.music_tools.music_catalog.add(catalog_source, "calm forest ambience", "lyria", "test")
        self.music_tools.music_catalog._reranker = lambda _prompt, _candidates: (entry["id"], 0.97)
        provider = MagicMock()
        self.music_tools._music_provider = provider

        result = self.music_tools.create_music("calm forest ambience", handle="forest")

        self.assertIn("Reused a matching private catalog track", result)
        provider.generate.assert_not_called()
        created = list(Path(self.music_tools.output_dir).glob("forest_*.mp3"))
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].read_bytes(), b"catalog audio")

    def test_play_music_cooldown(self):
        self.music_tools.cooldown_duration = 5.0
        res1 = self.music_tools.play_music("ambient")
        self.assertIn("Successfully started playing", res1)

        res2 = self.music_tools.play_music("combat")
        self.assertIn("play_music is on cooldown", res2)

    def test_use_generated_music_config_disabled(self):
        config = {
            "music": {
                "use_generated_music": False,
                "generation_cooldown": 25.0,
            }
        }
        tools = self.make_tools(config)
        self.assertFalse(tools.use_generated_music)
        self.assertEqual(tools.generation_cooldown, 25.0)

        res = tools.create_music("test prompt")
        self.assertIn("Error: Music generation is disabled", res)

    def test_create_music_cooldown(self):
        mock_provider = MagicMock()
        self.music_tools._music_provider = mock_provider
        self.music_tools.cooldown_duration = 5.0

        res1 = self.music_tools.create_music("epic theme 1")
        self.assertIn("Music generation started in background", res1)

        res2 = self.music_tools.create_music("epic theme 2")
        self.assertIn("create_music is on cooldown", res2)

        self.music_tools.join_generation(timeout=5.0)

    def test_switch_cooldown_config(self):
        config = {
            "music": {
                "use_generated_music": True,
                "generation_cooldown": 30.0,
                "switch_cooldown": 12.0,
            }
        }
        tools = self.make_tools(config)
        self.assertEqual(tools.generation_cooldown, 30.0)
        self.assertEqual(tools.switch_cooldown, 12.0)

    def test_style_default_loaded_from_config(self):
        config = {"music": {"style": "  orchestral epic  "}}
        tools = self.make_tools(config)
        self.assertEqual(tools.style_default, "orchestral epic")

    def test_style_default_appended_when_absent(self):
        config = {"music": {"style": "orchestral epic"}}
        tools = self.make_tools(config)
        result = tools._apply_default_style("a triumphant fanfare")
        self.assertEqual(result, "a triumphant fanfare\n\nStyle: orchestral epic")

    def test_style_default_not_appended_when_style_present(self):
        config = {"music": {"style": "orchestral epic"}}
        tools = self.make_tools(config)
        prompt = "a triumphant fanfare. Style: jazz"
        result = tools._apply_default_style(prompt)
        self.assertEqual(result, prompt)

    def test_style_default_empty_no_change(self):
        config = {"music": {}}
        tools = self.make_tools(config)
        prompt = "a triumphant fanfare"
        result = tools._apply_default_style(prompt)
        self.assertEqual(result, prompt)

if __name__ == "__main__":
    unittest.main()
