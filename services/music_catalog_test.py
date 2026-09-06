"""Tests for MusicCatalog service and registry integration."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from absl import flags
from absl.testing import flagsaver

import object_registry
from providers.text_response_provider import TextResponseResult
from services.music_catalog import (
    PROJECT_ROOT,
    MusicCatalog,
    ensure_music_catalog_root,
    get_music_catalog_root,
)
from storage.database import DatabaseManager


class TestMusicCatalogRootSelection(unittest.TestCase):
    def test_cloud_root_defaults_to_mnt_storage_music_catalog(self):
        orig_local = flags.FLAGS["testing_use_local"].value
        try:
            flags.FLAGS["testing_use_local"].value = False
            self.assertEqual(get_music_catalog_root(), Path("/mnt/storage/music_catalog"))
        finally:
            flags.FLAGS["testing_use_local"].value = orig_local

    @flagsaver.flagsaver(testing_use_local=True)
    def test_local_root_uses_workspace_music_catalog_directory(self):
        self.assertEqual(
            get_music_catalog_root().resolve(),
            (PROJECT_ROOT / "music_catalog").resolve(),
        )


class FakeMusicDb:
    def __init__(self):
        self.tracks = []

    def add_music_catalog_track(self, track_id, filename, prompt, provider, model, term_frequencies):
        self.tracks.append({
            "id": track_id,
            "filename": filename,
            "prompt": prompt,
            "provider": provider,
            "model": model,
        })

    def find_music_catalog_candidates(self, term_frequencies, limit):
        return [
            {"id": t["id"], "filename": t["filename"], "prompt": t["prompt"]}
            for t in self.tracks[:limit]
        ]


class TestMusicCatalog(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.catalog_dir = Path(self.temp_dir) / "catalog"
        self.catalog_dir.mkdir(parents=True, exist_ok=True)
        self.db = FakeMusicDb()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init_with_valid_arguments(self):
        catalog = MusicCatalog(directory=self.catalog_dir, database_manager=self.db, match_threshold=0.8, candidate_count=3)
        self.assertEqual(catalog.directory, self.catalog_dir)
        self.assertTrue(self.catalog_dir.exists())
        self.assertIs(catalog.database_manager, self.db)
        self.assertEqual(catalog.match_threshold, 0.8)
        self.assertEqual(catalog.candidate_count, 3)

    def test_init_requires_database_manager(self):
        with self.assertRaises(ValueError) as ctx:
            MusicCatalog(directory=self.catalog_dir, database_manager=None)
        self.assertIn("database_manager is required", str(ctx.exception))

    def test_init_defaults_directory_to_music_catalog_root(self):
        catalog = MusicCatalog(database_manager=self.db)
        self.assertEqual(catalog.directory, get_music_catalog_root())

    def test_init_missing_database_manager_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            MusicCatalog()
        self.assertIn("database_manager is required", str(ctx.exception))

    def test_from_config_with_mocked_database_manager(self):
        config = {
            "music": {
                "catalog_match_threshold": 0.75,
                "catalog_candidate_count": 8,
                "catalog_reranker_provider": "gemini-2-5",
                "catalog_reranker_model": "gemini-2.5-flash-lite",
            }
        }
        mock_db = MagicMock(spec=DatabaseManager)

        catalog = MusicCatalog.from_config(
            config=config,
            database_manager=mock_db,
            directory=self.catalog_dir,
        )
        self.assertEqual(catalog.directory, self.catalog_dir)
        self.assertEqual(catalog.match_threshold, 0.75)
        self.assertEqual(catalog.candidate_count, 8)
        self.assertIs(catalog.database_manager, mock_db)
        self.assertIsNotNone(catalog.reranker_provider)

    def test_from_config_defaults_directory(self):
        mock_db = MagicMock(spec=DatabaseManager)
        catalog = MusicCatalog.from_config(
            config={},
            database_manager=mock_db,
        )
        self.assertEqual(catalog.directory, get_music_catalog_root())

    def test_from_config_resolves_object_registry_db_when_unspecified(self):
        mock_db = MagicMock(spec=DatabaseManager)
        with patch.object(object_registry, "db", mock_db):
            catalog = MusicCatalog.from_config(
                config={},
                database_manager=None,
                directory=self.catalog_dir,
            )
            self.assertIs(catalog.database_manager, mock_db)

    def test_from_config_raises_when_no_database_available(self):
        with patch.object(object_registry, "db", None):
            with self.assertRaises(ValueError) as ctx:
                MusicCatalog.from_config(
                    config={},
                    database_manager=None,
                )
            self.assertIn("database_manager is required", str(ctx.exception))

    def test_find_match_with_mocked_database_manager(self):
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.find_music_catalog_candidates.return_value = [
            {"id": "mock_id", "filename": "track.mp3", "prompt": "ambient meadow sound"}
        ]
        (self.catalog_dir / "track.mp3").write_bytes(b"dummy audio")

        catalog = MusicCatalog(
            directory=self.catalog_dir,
            database_manager=mock_db,
            match_threshold=0.8,
        )
        catalog._reranker = lambda prompt, candidates: ("mock_id", 0.95)

        match = catalog.find_match("ambient meadow sound")

        self.assertIsNotNone(match)
        self.assertEqual(match["id"], "mock_id")
        self.assertEqual(match["score"], 0.95)
        self.assertEqual(match["path"], self.catalog_dir / "track.mp3")
        mock_db.find_music_catalog_candidates.assert_called_once()

    def test_add_with_mocked_database_manager(self):
        mock_db = MagicMock(spec=DatabaseManager)
        catalog = MusicCatalog(
            directory=self.catalog_dir,
            database_manager=mock_db,
        )
        source = Path(self.temp_dir) / "test.mp3"
        source.write_bytes(b"test data")

        entry = catalog.add(source, "heroic fanfare", "lyria", "v1")

        mock_db.add_music_catalog_track.assert_called_once()
        args = mock_db.add_music_catalog_track.call_args[0]
        self.assertEqual(args[0], entry["id"])
        self.assertEqual(args[1], entry["filename"])
        self.assertEqual(args[2], "heroic fanfare")
        self.assertEqual(args[3], "lyria")
        self.assertEqual(args[4], "v1")
        self.assertTrue((self.catalog_dir / entry["filename"]).exists())

    def test_find_match_rejected_below_threshold(self):
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.find_music_catalog_candidates.return_value = [
            {"id": "battle_id", "filename": "battle.mp3", "prompt": "battle drums"}
        ]
        (self.catalog_dir / "battle.mp3").write_bytes(b"dummy audio")

        catalog = MusicCatalog(
            directory=self.catalog_dir,
            database_manager=mock_db,
            match_threshold=0.85,
        )
        catalog._reranker = lambda prompt, candidates: ("battle_id", 0.70)
        match = catalog.find_match("battle drums")
        self.assertIsNone(match)

    def test_tokens(self):
        tokens = MusicCatalog._tokens("Epic Orchestral Battle: Part 1!")
        self.assertEqual(tokens, ["epic", "orchestral", "battle", "part", "1"])

    def test_rerank_with_provider(self):
        mock_provider = MagicMock()
        mock_provider.generate.return_value = TextResponseResult(
            text='```json\n{"id": "track123", "score": 0.95}\n```',
            provider="gemini",
            model="gemini-2.5-flash-lite",
        )
        catalog = MusicCatalog(
            directory=self.catalog_dir,
            database_manager=self.db,
            reranker_provider=mock_provider,
        )
        candidates = [{"id": "track123", "prompt": "calm music"}]
        approved = catalog._rerank("calm music", candidates)
        self.assertEqual(approved, ("track123", 0.95))
