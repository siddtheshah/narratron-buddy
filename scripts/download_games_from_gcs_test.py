"""Unit tests for scripts/download_games_from_gcs.py."""

import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import MagicMock

from scripts.download_games_from_gcs import (
    compute_file_md5,
    download_all_games,
    download_game_from_gcs,
    group_blobs_by_game,
    is_game_excluded,
    remove_dir_tree,
    slugify,
)


class TestDownloadGamesFromGCS(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.target_dir = Path(self.temp_dir.name) / "games"
        self.target_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_slugify(self) -> None:
        self.assertEqual(slugify("Wacky Chess"), "wacky-chess")
        self.assertEqual(slugify("The Trader!"), "the-trader")

    def test_group_blobs_by_game(self) -> None:
        mock_blob1 = MagicMock()
        mock_blob1.name = "games/wacky-chess/game/game.yaml"
        mock_blob2 = MagicMock()
        mock_blob2.name = "games/wacky-chess/metadata.json"
        mock_blob3 = MagicMock()
        mock_blob3.name = "games/the-trader/metadata.json"
        mock_blob_root = MagicMock()
        mock_blob_root.name = "games/"

        blobs = [mock_blob1, mock_blob2, mock_blob3, mock_blob_root]
        grouped = group_blobs_by_game(blobs, gcs_prefix="games")

        self.assertIn("wacky-chess", grouped)
        self.assertIn("the-trader", grouped)
        self.assertEqual(len(grouped["wacky-chess"]), 2)
        self.assertEqual(len(grouped["the-trader"]), 1)
        self.assertEqual(grouped["wacky-chess"][0][1], "game/game.yaml")

    def test_download_game_from_gcs_success(self) -> None:
        mock_meta_blob = MagicMock()
        mock_meta_blob.download_as_bytes.return_value = json.dumps({
            "id": "wacky-chess",
            "title": "Wacky Chess: Chaos Edition",
        }).encode("utf-8")

        mock_file_blob = MagicMock()
        mock_file_blob.download_as_bytes.return_value = b"version: 2\nname: Wacky Chess\n"

        items = [
            (mock_meta_blob, "metadata.json"),
            (mock_file_blob, "game/game.yaml"),
        ]

        result = download_game_from_gcs(
            game_slug="wacky-chess",
            items=items,
            target_dir=self.target_dir,
            overwrite=True,
            dry_run=False,
        )

        self.assertEqual(result.get("id"), "wacky-chess")
        self.assertEqual(result.get("title"), "Wacky Chess: Chaos Edition")
        self.assertEqual(result.get("files_count"), 2)

        saved_meta = self.target_dir / "wacky-chess" / "metadata.json"
        saved_yaml = self.target_dir / "wacky-chess" / "game" / "game.yaml"

        self.assertTrue(saved_meta.exists())
        self.assertTrue(saved_yaml.exists())
        self.assertEqual(saved_yaml.read_text(encoding="utf-8"), "version: 2\nname: Wacky Chess\n")

    def test_download_game_from_gcs_dry_run(self) -> None:
        mock_file_blob = MagicMock()
        mock_file_blob.size = 100

        items = [(mock_file_blob, "game/game.yaml")]

        result = download_game_from_gcs(
            game_slug="wacky-chess",
            items=items,
            target_dir=self.target_dir,
            overwrite=True,
            dry_run=True,
        )

        self.assertEqual(result.get("files_count"), 1)
        self.assertEqual(result.get("total_bytes"), 100)
        saved_yaml = self.target_dir / "wacky-chess" / "game" / "game.yaml"
        self.assertFalse(saved_yaml.exists())

    def test_download_all_games_with_filter(self) -> None:
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"

        blob1 = MagicMock()
        blob1.name = "games/wacky-chess/metadata.json"
        blob1.download_as_bytes.return_value = json.dumps({"title": "Wacky Chess"}).encode("utf-8")

        blob2 = MagicMock()
        blob2.name = "games/the-trader/metadata.json"
        blob2.download_as_bytes.return_value = json.dumps({"title": "The Trader"}).encode("utf-8")

        mock_bucket.list_blobs.return_value = [blob1, blob2]

        results = download_all_games(
            bucket=mock_bucket,
            gcs_prefix="games",
            target_dir=self.target_dir,
            game_filter="the-trader",
            overwrite=True,
            dry_run=False,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get("id"), "the-trader")
        self.assertTrue((self.target_dir / "the-trader" / "metadata.json").exists())
        self.assertFalse((self.target_dir / "wacky-chess").exists())

    def test_is_game_excluded(self) -> None:
        self.assertTrue(is_game_excluded("example_game"))
        self.assertTrue(is_game_excluded("example-game"))
        self.assertTrue(is_game_excluded("Example Game"))
        self.assertFalse(is_game_excluded("the-trader"))

    def test_group_blobs_omits_example_game(self) -> None:
        blob_ex = MagicMock()
        blob_ex.name = "games/example_game/metadata.json"
        blob_real = MagicMock()
        blob_real.name = "games/the-trader/metadata.json"

        grouped = group_blobs_by_game([blob_ex, blob_real], gcs_prefix="games")
        self.assertNotIn("example_game", grouped)
        self.assertNotIn("example-game", grouped)
        self.assertIn("the-trader", grouped)

    def test_download_game_skips_excluded_example(self) -> None:
        mock_blob = MagicMock()
        items = [(mock_blob, "metadata.json")]
        res = download_game_from_gcs(
            game_slug="example_game",
            items=items,
            target_dir=self.target_dir,
        )
        self.assertTrue(res.get("excluded"))
        self.assertEqual(res.get("files_count"), 0)
        mock_blob.download_to_filename.assert_not_called()
        mock_blob.download_as_bytes.assert_not_called()

    def test_download_all_games_filter_excluded_example(self) -> None:
        mock_bucket = MagicMock()
        results = download_all_games(
            bucket=mock_bucket,
            gcs_prefix="games",
            target_dir=self.target_dir,
            game_filter="example_game",
        )
        self.assertEqual(results, [])
        mock_bucket.list_blobs.assert_not_called()

    def test_download_game_prunes_deleted_upstream_file(self) -> None:
        game_path = self.target_dir / "wacky-chess"
        (game_path / "stamps").mkdir(parents=True, exist_ok=True)
        deleted_upstream_file = game_path / "stamps" / "deleted_upstream.png"
        deleted_upstream_file.write_bytes(b"old")

        mock_meta = MagicMock()
        mock_meta.download_as_bytes.return_value = json.dumps({"title": "Wacky Chess"}).encode("utf-8")
        items = [(mock_meta, "metadata.json")]

        download_game_from_gcs(
            game_slug="wacky-chess",
            items=items,
            target_dir=self.target_dir,
            overwrite=False,
        )

        self.assertFalse(deleted_upstream_file.exists())
        self.assertTrue((game_path / "metadata.json").exists())

    def test_remove_dir_tree_handles_readonly_file(self) -> None:
        test_dir = self.target_dir / "readonly_test"
        test_dir.mkdir(parents=True, exist_ok=True)
        ro_file = test_dir / "readonly.txt"
        ro_file.write_text("locked", encoding="utf-8")
        ro_file.chmod(stat.S_IREAD)

        remove_dir_tree(test_dir)
        self.assertFalse(test_dir.exists())

    def test_download_game_diff_skips_unchanged_files(self) -> None:
        game_path = self.target_dir / "wacky-chess" / "game"
        game_path.mkdir(parents=True, exist_ok=True)
        yaml_path = game_path / "game.yaml"
        yaml_path.write_text("version: 2\n", encoding="utf-8")

        mock_yaml_blob = MagicMock()
        mock_yaml_blob.size = yaml_path.stat().st_size
        mock_yaml_blob.md5_hash = compute_file_md5(yaml_path)

        mock_new_blob = MagicMock()
        mock_new_blob.size = 50
        mock_new_blob.md5_hash = "different=="
        mock_new_blob.download_as_bytes.return_value = b"new file content"

        items = [
            (mock_yaml_blob, "game/game.yaml"),
            (mock_new_blob, "game/panel.html"),
        ]

        result = download_game_from_gcs(
            game_slug="wacky-chess",
            items=items,
            target_dir=self.target_dir,
            diff=True,
            prune=True,
        )

        self.assertEqual(result.get("skipped_count"), 1)
        self.assertIn("game/game.yaml", result.get("skipped_files", []))
        self.assertEqual(result.get("files_count"), 1)
        self.assertIn("game/panel.html", result.get("downloaded_files", []))


if __name__ == "__main__":
    unittest.main()
