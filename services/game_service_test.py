"""Unit tests for services/game_service.py."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from absl.testing import flagsaver
from google.cloud import storage
from pydantic import JsonValue
import pytest

import services.game_service as games
from services.game_service import (
    FLAGS,
    GameService,
    get_games_root,
)


@pytest.mark.parametrize("prefix", ["games", "/custom/games/", ""])
def test_cloud_game_download_uses_python_transfer_manager(tmp_path: Path, prefix: str) -> None:
    mount = tmp_path / "mount"
    folder = mount / "Actual Game"
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text('{"id": "checkers-game"}', encoding="utf-8")
    assets = {
        "game/game.yaml": b"version: 2\n",
        "game/panel.html": b"<div>Status</div>",
        "game/crest.svg": b'<svg viewBox="0 0 10 10"/>',
        "game/entity_schema.json": b'{"type": "object"}',
        "stamps/crown.png": b"stamp",
        "metadata.json": b'{"id": "checkers-game"}',
        "README.md": b"# Game readme",
    }
    bucket = MagicMock(spec=storage.Bucket)
    bucket.name = "custom-bucket"
    root = prefix.strip("/")
    object_prefix = f"{root}/Actual Game/" if root else "Actual Game/"
    bucket.list_blobs.return_value = [SimpleNamespace(name=object_prefix + name) for name in assets]
    bucket.list_blobs.return_value.append(SimpleNamespace(name=object_prefix + "game/"))
    temporary_paths: list[Path] = []

    def download(
        source: storage.Bucket, names: list[str], *, destination_directory: str,
        blob_name_prefix: str, worker_type: str, max_workers: int, raise_exception: bool,
    ) -> list[None]:
        assert source is bucket
        assert blob_name_prefix == object_prefix
        assert worker_type == games.transfer_manager.THREAD
        assert max_workers == 8
        assert raise_exception
        destination = Path(destination_directory)
        temporary_paths.append(destination)
        for name in names:
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(assets[name])
        return [None for _ in names]

    service = GameService(mount)
    with flagsaver.flagsaver(testing_use_local=False), \
         patch.object(games, "get_games_root", return_value=mount), \
         patch.dict("os.environ", {"GCS_BUCKET": "custom-bucket", "GCS_GAMES_PREFIX": prefix}), \
         patch.object(games.storage, "Client", autospec=True) as client, \
         patch.object(games.transfer_manager, "download_many_to_path", side_effect=download) as transfer:
        client.return_value.bucket.return_value = bucket
        loaded = service.load_game_assets("checkers-game")
        client.return_value.bucket.assert_called_once_with("custom-bucket")
        bucket.list_blobs.assert_called_once_with(prefix=object_prefix)
        transfer.assert_called_once()

    assert loaded == {
        "game/game.yaml": b"version: 2\n",
        "game/panel.html": b"<div>Status</div>",
        "game/crest.svg": b'<svg viewBox="0 0 10 10"/>',
        "game/entity_schema.json": b'{"type": "object"}',
        "stamps/crown.png": b"stamp",
    }
    assert not temporary_paths[0].exists()


@pytest.mark.parametrize("local_mode", [True, False])
def test_explicit_local_game_directory_does_not_contact_gcs(tmp_path: Path, local_mode: bool) -> None:
    folder = tmp_path / "local-game"
    (folder / "game").mkdir(parents=True)
    (folder / "game" / "game.yaml").write_bytes(b"version: 2\n")
    with flagsaver.flagsaver(testing_use_local=local_mode), \
         patch.object(games.storage, "Client", autospec=True) as client:
        assert GameService(tmp_path).load_game_assets("local-game") == {"game/game.yaml": b"version: 2\n"}
        client.assert_not_called()


@pytest.mark.parametrize("name", ["../outside.txt", "/absolute.txt", "game/../../outside.txt", "C:/outside.txt"])
def test_cloud_game_rejects_unsafe_names_before_downloading(tmp_path: Path, name: str) -> None:
    bucket = MagicMock(spec=storage.Bucket)
    bucket.list_blobs.return_value = [SimpleNamespace(name="games/stage/" + name)]
    with patch.dict("os.environ", {"GCS_GAMES_PREFIX": "games"}), \
         patch.object(games.storage, "Client", autospec=True) as client, \
         patch.object(games.transfer_manager, "download_many_to_path") as transfer:
        client.return_value.bucket.return_value = bucket
        with pytest.raises(ValueError):
            GameService._download_game_folder("stage", tmp_path)
        transfer.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_cloud_game_download_failure_cleans_up_temporary_package(tmp_path: Path) -> None:
    folder = tmp_path / "stage"
    folder.mkdir()
    service = GameService(tmp_path)
    temporary_paths: list[Path] = []

    def fail_download(folder_name: str, destination: Path) -> None:
        assert folder_name == "stage"
        temporary_paths.append(destination)
        (destination / "partial.txt").write_bytes(b"partial")
        raise OSError("Download failed")

    with flagsaver.flagsaver(testing_use_local=False), \
         patch.object(games, "get_games_root", return_value=tmp_path), \
         patch.object(service, "_download_game_folder", side_effect=fail_download), \
         pytest.raises(OSError, match="Download failed"):
        service.load_game_assets("stage")
    assert not temporary_paths[0].exists()


def test_cloud_game_empty_prefix_is_reported(tmp_path: Path) -> None:
    with patch.object(games.storage, "Client", autospec=True) as client, \
         patch.object(games.transfer_manager, "download_many_to_path") as transfer:
        client.return_value.bucket.return_value.list_blobs.return_value = []
        with pytest.raises(FileNotFoundError, match="No game files"):
            GameService._download_game_folder("stage", tmp_path)
        transfer.assert_not_called()


class TestGameService(unittest.TestCase):
    """Tests for GameService listing, sorting, cover loading, and asset extraction."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.local_dir = Path(self.temp_dir.name)

        self.game1_dir = self.local_dir / "Alpha Checkers"
        (self.game1_dir / "game").mkdir(parents=True)
        (self.game1_dir / "stamps").mkdir(parents=True)
        (self.game1_dir / "metadata.json").write_text(
            json.dumps({
                "id": "alpha-checkers",
                "title": "Alpha Checkers",
                "description": "First checkers game.",
                "created_at": "2026-08-10T10:00:00Z",
                "cover_image": "game/crest.svg",
                "tags": ["Board Game", "Strategy"],
            }),
            encoding="utf-8",
        )
        (self.game1_dir / "game" / "game.yaml").write_text("version: 2\n", encoding="utf-8")
        (self.game1_dir / "game" / "panel.html").write_text("<div>Checkers</div>", encoding="utf-8")
        (self.game1_dir / "game" / "crest.svg").write_text('<svg viewBox="0 0 10 10"/>', encoding="utf-8")
        (self.game1_dir / "game" / "game_test.py").write_text("def test_noop(): pass\n", encoding="utf-8")
        (self.game1_dir / "stamps" / "crown.png").write_bytes(b"\x89PNG\r\n\x1a\ncrown")

        self.game2_dir = self.local_dir / "Beta Chess"
        (self.game2_dir / "game").mkdir(parents=True)
        (self.game2_dir / "metadata.json").write_text(
            json.dumps({
                "id": "beta-chess",
                "title": "Beta Chess",
                "description": "Second chess game.",
                "created_at": "2026-08-20T12:00:00Z",
                "tags": ["Chess"],
            }),
            encoding="utf-8",
        )
        (self.game2_dir / "game" / "game.json").write_text('{"version": 2}', encoding="utf-8")
        (self.game2_dir / "game" / "king.svg").write_text('<svg viewBox="0 0 10 10"/>', encoding="utf-8")
        (self.game2_dir / "game" / "queen.svg").write_text('<svg viewBox="0 0 10 10"/>', encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_list_games_sorted_newest_first(self) -> None:
        service = GameService(base_dir=self.local_dir)
        listed = service.list_games(force_refresh=True)

        self.assertEqual(len(listed), 2)
        self.assertEqual(listed[0]["id"], "beta-chess")
        self.assertEqual(listed[1]["id"], "alpha-checkers")
        self.assertEqual(listed[0]["declaration_file"], "game/game.json")
        self.assertEqual(listed[0]["has_panel"], False)
        self.assertEqual(listed[0]["svg_count"], 2)
        self.assertEqual(listed[0]["stamp_count"], 0)
        self.assertEqual(listed[1]["declaration_file"], "game/game.yaml")
        self.assertEqual(listed[1]["has_panel"], True)
        self.assertEqual(listed[1]["svg_count"], 1)
        self.assertEqual(listed[1]["stamp_count"], 1)

    def test_get_game_and_cover(self) -> None:
        service = GameService(base_dir=self.local_dir)
        game = service.get_game("alpha-checkers")
        self.assertIsNotNone(game)
        assert game is not None
        self.assertEqual(game["title"], "Alpha Checkers")

        cover_res = service.get_game_cover("alpha-checkers")
        self.assertIsNotNone(cover_res)
        assert cover_res is not None
        cover_data, ctype = cover_res
        self.assertEqual(cover_data, b'<svg viewBox="0 0 10 10"/>')
        self.assertIn("svg", ctype.lower())

    def test_load_game_assets_excludes_python_tests_and_metadata(self) -> None:
        service = GameService(base_dir=self.local_dir)
        assets = service.load_game_assets("alpha-checkers")
        self.assertEqual(set(assets.keys()), {
            "game/game.yaml",
            "game/panel.html",
            "game/crest.svg",
            "stamps/crown.png",
        })

    def test_cache_invalidation_and_refresh(self) -> None:
        service = GameService(base_dir=self.local_dir)
        self.assertEqual(len(service.list_games()), 2)

        game3_dir = self.local_dir / "Gamma Trade"
        (game3_dir / "game").mkdir(parents=True)
        (game3_dir / "metadata.json").write_text(
            json.dumps({
                "id": "gamma-trade",
                "title": "Gamma Trade",
                "created_at": "2026-08-25T00:00:00Z",
            }),
            encoding="utf-8",
        )
        (game3_dir / "game" / "game.yaml").write_text("version: 2\n", encoding="utf-8")

        self.assertEqual(len(service.list_games()), 2)
        self.assertTrue(service.refresh_if_changed())
        self.assertEqual(len(service.list_games()), 3)
        self.assertEqual(service.list_games()[0]["id"], "gamma-trade")

        service.invalidate_cache()
        self.assertEqual(len(service.list_games()), 3)

    def test_get_games_root_flags(self) -> None:
        orig_local = FLAGS["testing_use_local"].value
        try:
            FLAGS["testing_use_local"].value = False
            self.assertEqual(get_games_root(), Path("/mnt/storage/games"))

            FLAGS["testing_use_local"].value = True
            self.assertEqual(get_games_root().name, "games")
        finally:
            FLAGS["testing_use_local"].value = orig_local

    def test_warm_listing_does_not_access_storage(self) -> None:
        service = GameService(self.local_dir)
        expected = service.list_games()
        with patch.object(service, "_get_cache_key", side_effect=AssertionError("Unexpected storage scan")), \
             patch.object(service, "_fetch_games", side_effect=AssertionError("Unexpected reload")):
            self.assertEqual(service.list_games(), expected)

    def test_refresh_serves_previous_snapshot_until_complete(self) -> None:
        service = GameService(self.local_dir)
        expected = service.list_games()
        original_fetch = service._fetch_games

        def fetch_during_refresh() -> list[dict[str, JsonValue]]:
            self.assertIs(service.list_games(), expected)
            return original_fetch()

        with patch.object(service, "_fetch_games", side_effect=fetch_during_refresh):
            self.assertTrue(service.refresh_if_changed(force_refresh=True))
