"""Unit tests for scripts/upload_games_to_gcs.py."""

import json
from pathlib import Path
import tempfile
from typing import List
import unittest
from unittest.mock import MagicMock

from scripts.upload_games_to_gcs import (
    StorageBlobProtocol,
    clear_exact_matching_game,
    collect_game_files,
    compute_file_crc32c,
    compute_file_md5,
    create_or_load_metadata,
    has_game_manifest,
    is_file_changed,
    is_game_excluded,
    slugify,
    upload_game_to_gcs,
    validate_game_yaml_files,
)


class TestUploadGamesToGCS(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.game_dir = Path(self.temp_dir.name) / "Wacky Chess"
        (self.game_dir / "game").mkdir(parents=True)

        (self.game_dir / "game" / "game.yaml").write_text(
            "version: 2\nname: Wacky Chess\n", encoding="utf-8"
        )
        (self.game_dir / "game" / "crest.svg").write_text(
            "<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8"
        )
        (self.game_dir / "metadata.json").write_text(
            json.dumps({
                "id": "wacky-chess",
                "title": "Wacky Chess: Chaos Edition",
                "description": "Chaotic board game.",
                "created_at": "2026-08-18T18:00:00Z",
            }),
            encoding="utf-8",
        )
        stamps = self.game_dir / "stamps"
        stamps.mkdir()
        (stamps / "pawn.png").write_bytes(b"fakepng")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_slugify(self) -> None:
        self.assertEqual(slugify("Wacky Chess"), "wacky-chess")
        self.assertEqual(slugify("Peaks of Tianmu!"), "peaks-of-tianmu")

    def test_has_game_manifest(self) -> None:
        self.assertTrue(has_game_manifest(self.game_dir))
        empty_dir = Path(self.temp_dir.name) / "Empty"
        empty_dir.mkdir()
        self.assertFalse(has_game_manifest(empty_dir))

    def test_create_or_load_metadata(self) -> None:
        meta = create_or_load_metadata(self.game_dir)
        self.assertEqual(meta["id"], "wacky-chess")
        self.assertEqual(meta["title"], "Wacky Chess: Chaos Edition")
        self.assertEqual(meta["cover_image"], "game/crest.svg")

    def test_create_or_load_metadata_generates_default(self) -> None:
        new_game = Path(self.temp_dir.name) / "My Custom Board"
        (new_game / "game").mkdir(parents=True)
        (new_game / "game" / "game.yaml").write_text("version: 2\n", encoding="utf-8")
        meta = create_or_load_metadata(new_game)
        self.assertEqual(meta["id"], "my-custom-board")
        self.assertEqual(meta["title"], "My Custom Board")
        self.assertEqual(meta["genre"], "Board Game")
        self.assertTrue((new_game / "metadata.json").exists())

    def test_collect_game_files(self) -> None:
        files = collect_game_files(self.game_dir)
        filenames = [f.name for f in files]
        self.assertIn("game.yaml", filenames)
        self.assertIn("crest.svg", filenames)
        self.assertIn("metadata.json", filenames)
        self.assertIn("pawn.png", filenames)

    def test_clear_exact_matching_game_success(self) -> None:
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"
        mock_blob1 = MagicMock()
        mock_blob1.name = "games/wacky-chess/metadata.json"
        mock_blob1.download_as_bytes.return_value = json.dumps({
            "id": "wacky-chess",
            "title": "Wacky Chess: Chaos Edition",
        }).encode("utf-8")

        mock_blob2 = MagicMock()
        mock_blob2.name = "games/wacky-chess/game/game.yaml"

        mock_bucket.list_blobs.return_value = [mock_blob1, mock_blob2]

        deleted_count = clear_exact_matching_game(
            bucket=mock_bucket,
            gcs_prefix="games",
            game_slug="wacky-chess",
            game_title="Wacky Chess: Chaos Edition",
            dry_run=False,
        )

        self.assertEqual(deleted_count, 2)
        mock_bucket.delete_blobs.assert_called_once()

    def test_clear_exact_matching_game_mismatched_metadata_aborts(self) -> None:
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"
        mock_blob1 = MagicMock()
        mock_blob1.name = "games/wacky-chess/metadata.json"
        mock_blob1.download_as_bytes.return_value = json.dumps({
            "id": "completely-different-game",
            "title": "Unrelated Game",
        }).encode("utf-8")

        mock_bucket.list_blobs.return_value = [mock_blob1]

        deleted_count = clear_exact_matching_game(
            bucket=mock_bucket,
            gcs_prefix="games",
            game_slug="wacky-chess",
            game_title="Wacky Chess: Chaos Edition",
            dry_run=False,
        )

        self.assertEqual(deleted_count, 0)
        mock_blob1.delete.assert_not_called()

    def test_clear_exact_matching_game_unsafe_slug_aborts(self) -> None:
        mock_bucket = MagicMock()
        for unsafe_slug in ["", "/", ".", "*", "a"]:
            deleted = clear_exact_matching_game(
                bucket=mock_bucket,
                gcs_prefix="games",
                game_slug=unsafe_slug,
                game_title="Test",
                dry_run=False,
            )
            self.assertEqual(deleted, 0)
            mock_bucket.list_blobs.assert_not_called()

    def test_clear_exact_matching_game_prefix_mismatch_aborts(self) -> None:
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"
        corrupt_blob = MagicMock()
        corrupt_blob.name = "games/other-folder/file.txt"
        mock_bucket.list_blobs.return_value = [corrupt_blob]

        deleted = clear_exact_matching_game(
            bucket=mock_bucket,
            gcs_prefix="games",
            game_slug="wacky-chess",
            game_title="Wacky Chess",
            dry_run=False,
        )

        self.assertEqual(deleted, 0)
        corrupt_blob.delete.assert_not_called()

    def test_upload_game_with_clearing(self) -> None:
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"
        old_blob = MagicMock()
        old_blob.name = "games/wacky-chess/stale_file.txt"

        def mock_list_blobs(prefix: str = "") -> List[StorageBlobProtocol]:
            if prefix == "games/wacky-chess/":
                return [old_blob]
            return []

        mock_bucket.list_blobs.side_effect = mock_list_blobs
        mock_target_blob = MagicMock()
        mock_bucket.blob.return_value = mock_target_blob

        result = upload_game_to_gcs(
            game_dir=self.game_dir,
            bucket=mock_bucket,
            gcs_prefix="games",
            clear_existing=True,
            dry_run=False,
        )

        self.assertEqual(result.get("id"), "wacky-chess")
        self.assertEqual(result.get("cleared_count"), 1)
        self.assertEqual(result.get("files_count"), 4)
        self.assertEqual(mock_target_blob.upload_from_filename.call_count, 4)

    def test_upload_game_dry_run(self) -> None:
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"
        old_blob = MagicMock()
        old_blob.name = "games/wacky-chess/stale_file.txt"

        def mock_list_blobs(prefix: str = "") -> List[StorageBlobProtocol]:
            if prefix == "games/wacky-chess/":
                return [old_blob]
            return []

        mock_bucket.list_blobs.side_effect = mock_list_blobs
        mock_target_blob = MagicMock()
        mock_bucket.blob.return_value = mock_target_blob

        result = upload_game_to_gcs(
            game_dir=self.game_dir,
            bucket=mock_bucket,
            gcs_prefix="games",
            clear_existing=True,
            dry_run=True,
        )

        self.assertEqual(result.get("id"), "wacky-chess")
        self.assertEqual(result.get("cleared_count"), 1)
        self.assertEqual(result.get("files_count"), 4)
        old_blob.delete.assert_not_called()
        mock_target_blob.upload_from_filename.assert_not_called()

    def test_upload_game_diff_skips_identical_files(self) -> None:
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"

        create_or_load_metadata(self.game_dir)

        remote_blobs = []
        files = collect_game_files(self.game_dir)
        for f in files:
            rel = f.relative_to(self.game_dir).as_posix()
            b = MagicMock()
            b.name = f"games/wacky-chess/{rel}"
            b.size = f.stat().st_size
            b.md5_hash = compute_file_md5(f)
            b.crc32c = compute_file_crc32c(f)
            remote_blobs.append(b)

        mock_bucket.list_blobs.return_value = remote_blobs
        mock_target_blob = MagicMock()
        mock_bucket.blob.return_value = mock_target_blob

        result = upload_game_to_gcs(
            game_dir=self.game_dir,
            bucket=mock_bucket,
            gcs_prefix="games",
            clear_existing=False,
            diff=True,
            prune=False,
            dry_run=False,
        )

        self.assertEqual(result.get("files_count"), 0)
        self.assertEqual(result.get("skipped_count"), 4)
        self.assertEqual(result.get("total_bytes"), 0)
        mock_target_blob.upload_from_filename.assert_not_called()

    def test_upload_game_diff_prunes_orphans(self) -> None:
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"

        orphaned_blob = MagicMock()
        orphaned_blob.name = "games/wacky-chess/deprecated.txt"

        mock_bucket.list_blobs.return_value = [orphaned_blob]
        mock_target_blob = MagicMock()
        mock_bucket.blob.return_value = mock_target_blob

        result = upload_game_to_gcs(
            game_dir=self.game_dir,
            bucket=mock_bucket,
            gcs_prefix="games",
            clear_existing=False,
            diff=True,
            prune=True,
            dry_run=False,
        )

        self.assertEqual(result.get("pruned_count"), 1)
        orphaned_blob.delete.assert_called_once()

    def test_is_game_excluded(self) -> None:
        self.assertTrue(is_game_excluded("example_game"))
        self.assertTrue(is_game_excluded("example-game"))
        self.assertTrue(is_game_excluded("Example Game"))
        self.assertFalse(is_game_excluded("wacky-chess"))
        self.assertFalse(is_game_excluded("peaks-of-tianmu"))

    def test_upload_game_skips_excluded_example(self) -> None:
        mock_bucket = MagicMock()
        example_dir = Path(self.temp_dir.name) / "example_game"
        (example_dir / "game").mkdir(parents=True)
        (example_dir / "game" / "game.yaml").write_text("version: 2\n", encoding="utf-8")
        (example_dir / "metadata.json").write_text(
            json.dumps({"id": "example-game", "title": "Example Game"}),
            encoding="utf-8",
        )
        res = upload_game_to_gcs(
            game_dir=example_dir,
            bucket=mock_bucket,
            gcs_prefix="games",
        )
        self.assertTrue(res.get("excluded"))
        self.assertEqual(res.get("files_count"), 0)
        mock_bucket.blob.assert_not_called()

    def test_validate_game_yaml_files_success(self) -> None:
        yaml_files = validate_game_yaml_files(self.game_dir)
        self.assertEqual(len(yaml_files), 1)
        self.assertEqual(yaml_files[0].name, "game.yaml")

    def test_validate_game_yaml_files_syntax_error(self) -> None:
        bad_yaml = self.game_dir / "game" / "broken.yaml"
        bad_yaml.write_text("key: [unclosed list", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            validate_game_yaml_files(self.game_dir)
        self.assertIn("Failed to parse YAML file in game", str(ctx.exception))
        self.assertIn("broken.yaml", str(ctx.exception))

    def test_validate_game_yaml_files_duplicate_keys(self) -> None:
        dup_yaml = self.game_dir / "game" / "duplicate.yaml"
        dup_yaml.write_text("item:\n  sub: 1\n  sub: 2\n", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            validate_game_yaml_files(self.game_dir)
        self.assertIn("duplicate key 'sub'", str(ctx.exception))

    def test_is_file_changed_scenarios(self) -> None:
        file_path = self.game_dir / "game" / "game.yaml"
        file_size = file_path.stat().st_size
        file_md5 = compute_file_md5(file_path)
        file_crc = compute_file_crc32c(file_path)

        self.assertTrue(is_file_changed(file_path, None))

        mock_blob = MagicMock()
        mock_blob.size = file_size + 10
        mock_blob.md5_hash = file_md5
        self.assertTrue(is_file_changed(file_path, mock_blob))

        mock_blob.size = file_size
        mock_blob.md5_hash = file_md5
        mock_blob.crc32c = file_crc
        self.assertFalse(is_file_changed(file_path, mock_blob))


if __name__ == "__main__":
    unittest.main()
