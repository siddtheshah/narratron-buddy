"""Unit tests for scripts/download_adventures_from_gcs.py."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from scripts.download_adventures_from_gcs import (
    download_adventure_from_gcs,
    download_all_adventures,
    group_blobs_by_adventure,
    slugify,
    is_adventure_excluded,
)


class TestDownloadAdventuresFromGCS(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.target_dir = Path(self.temp_dir.name) / "adventures"
        self.target_dir.mkdir(parents=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_slugify(self):
        self.assertEqual(slugify("Lesovik Station"), "lesovik-station")
        self.assertEqual(slugify("The Trader!"), "the-trader")

    def test_group_blobs_by_adventure(self):
        mock_blob1 = MagicMock()
        mock_blob1.name = "adventures/lesovik-station/theater.yaml"
        mock_blob2 = MagicMock()
        mock_blob2.name = "adventures/lesovik-station/metadata.json"
        mock_blob3 = MagicMock()
        mock_blob3.name = "adventures/the-trader/metadata.json"
        mock_blob_root = MagicMock()
        mock_blob_root.name = "adventures/"

        blobs = [mock_blob1, mock_blob2, mock_blob3, mock_blob_root]
        grouped = group_blobs_by_adventure(blobs, gcs_prefix="adventures")

        self.assertIn("lesovik-station", grouped)
        self.assertIn("the-trader", grouped)
        self.assertEqual(len(grouped["lesovik-station"]), 2)
        self.assertEqual(len(grouped["the-trader"]), 1)
        self.assertEqual(grouped["lesovik-station"][0][1], "theater.yaml")

    def test_download_adventure_from_gcs_success(self):
        mock_meta_blob = MagicMock()
        mock_meta_blob.download_as_bytes.return_value = json.dumps({
            "id": "lesovik-station",
            "title": "Lesovik Station: Arctic Mystery",
        }).encode("utf-8")

        mock_file_blob = MagicMock()
        mock_file_blob.download_as_bytes.return_value = b"agent:\n  style: mystery\n"

        items = [
            (mock_meta_blob, "metadata.json"),
            (mock_file_blob, "theater.yaml"),
        ]

        result = download_adventure_from_gcs(
            adventure_slug="lesovik-station",
            items=items,
            target_dir=self.target_dir,
            overwrite=True,
            dry_run=False,
        )

        self.assertEqual(result["id"], "lesovik-station")
        self.assertEqual(result["title"], "Lesovik Station: Arctic Mystery")
        self.assertEqual(result["files_count"], 2)

        saved_meta = self.target_dir / "lesovik-station" / "metadata.json"
        saved_yaml = self.target_dir / "lesovik-station" / "theater.yaml"

        self.assertTrue(saved_meta.exists())
        self.assertTrue(saved_yaml.exists())
        self.assertEqual(saved_yaml.read_text(encoding="utf-8"), "agent:\n  style: mystery\n")

    def test_download_adventure_from_gcs_dry_run(self):
        mock_file_blob = MagicMock()
        mock_file_blob.size = 100

        items = [(mock_file_blob, "theater.yaml")]

        result = download_adventure_from_gcs(
            adventure_slug="lesovik-station",
            items=items,
            target_dir=self.target_dir,
            overwrite=True,
            dry_run=True,
        )

        self.assertEqual(result["files_count"], 1)
        self.assertEqual(result["total_bytes"], 100)
        saved_yaml = self.target_dir / "lesovik-station" / "theater.yaml"
        self.assertFalse(saved_yaml.exists())

    def test_download_all_adventures_with_filter(self):
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"

        blob1 = MagicMock()
        blob1.name = "adventures/lesovik-station/metadata.json"
        blob1.download_as_bytes.return_value = json.dumps({"title": "Lesovik Station"}).encode("utf-8")

        blob2 = MagicMock()
        blob2.name = "adventures/the-trader/metadata.json"
        blob2.download_as_bytes.return_value = json.dumps({"title": "The Trader"}).encode("utf-8")

        mock_bucket.list_blobs.return_value = [blob1, blob2]

        results = download_all_adventures(
            bucket=mock_bucket,
            gcs_prefix="adventures",
            target_dir=self.target_dir,
            adventure_filter="the-trader",
            overwrite=True,
            dry_run=False,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "the-trader")
        self.assertTrue((self.target_dir / "the-trader" / "metadata.json").exists())
        self.assertFalse((self.target_dir / "lesovik-station").exists())

    def test_is_adventure_excluded(self):
        self.assertTrue(is_adventure_excluded("example_adventure"))
        self.assertTrue(is_adventure_excluded("example-adventure"))
        self.assertTrue(is_adventure_excluded("Example Adventure"))
        self.assertFalse(is_adventure_excluded("the-trader"))

    def test_group_blobs_omits_example_adventure(self):
        blob_ex = MagicMock()
        blob_ex.name = "adventures/example_adventure/metadata.json"
        blob_real = MagicMock()
        blob_real.name = "adventures/the-trader/metadata.json"

        grouped = group_blobs_by_adventure([blob_ex, blob_real], gcs_prefix="adventures")
        self.assertNotIn("example_adventure", grouped)
        self.assertNotIn("example-adventure", grouped)
        self.assertIn("the-trader", grouped)

    def test_download_adventure_skips_excluded_example(self):
        mock_blob = MagicMock()
        items = [(mock_blob, "metadata.json")]
        res = download_adventure_from_gcs(
            adventure_slug="example_adventure",
            items=items,
            target_dir=self.target_dir,
        )
        self.assertTrue(res.get("excluded"))
        self.assertEqual(res["files_count"], 0)
        mock_blob.download_to_filename.assert_not_called()
        mock_blob.download_as_bytes.assert_not_called()

    def test_download_all_adventures_filter_excluded_example(self):
        mock_bucket = MagicMock()
        results = download_all_adventures(
            bucket=mock_bucket,
            gcs_prefix="adventures",
            target_dir=self.target_dir,
            adventure_filter="example_adventure",
        )
        self.assertEqual(results, [])
        mock_bucket.list_blobs.assert_not_called()


    def test_download_adventure_default_overwrites_and_clears_old_local_files(self) -> None:
        adv_path = self.target_dir / "lesovik-station"
        adv_path.mkdir(parents=True, exist_ok=True)
        stale_file = adv_path / "old_stale.txt"
        stale_file.write_text("old content", encoding="utf-8")

        mock_meta_blob = MagicMock()
        mock_meta_blob.download_as_bytes.return_value = json.dumps({
            "id": "lesovik-station",
            "title": "Lesovik Station: Arctic Mystery",
        }).encode("utf-8")

        mock_file_blob = MagicMock()
        mock_file_blob.download_as_bytes.return_value = b"agent:\n  style: mystery\n"

        items = [
            (mock_meta_blob, "metadata.json"),
            (mock_file_blob, "theater.yaml"),
        ]

        # Call with default overwrite (overwrite=True by default)
        result = download_adventure_from_gcs(
            adventure_slug="lesovik-station",
            items=items,
            target_dir=self.target_dir,
        )

        self.assertEqual(result["files_count"], 2)
        self.assertFalse(stale_file.exists())
        self.assertTrue((adv_path / "metadata.json").exists())
        self.assertTrue((adv_path / "theater.yaml").exists())

    def test_download_adventure_deletes_locally_when_file_deleted_upstream(self) -> None:
        adv_path = self.target_dir / "lesovik-station"
        adv_path.mkdir(parents=True, exist_ok=True)
        deleted_upstream_file = adv_path / "deleted_upstream.txt"
        deleted_upstream_file.write_text("file deleted in upstream GCS", encoding="utf-8")

        mock_meta = MagicMock()
        mock_meta.download_as_bytes.return_value = json.dumps({"title": "Lesovik"}).encode("utf-8")
        items = [(mock_meta, "metadata.json")]

        download_adventure_from_gcs(
            adventure_slug="lesovik-station",
            items=items,
            target_dir=self.target_dir,
            overwrite=True,
        )

        # File deleted upstream must be removed locally
        self.assertFalse(deleted_upstream_file.exists())
        self.assertTrue((adv_path / "metadata.json").exists())

    def test_download_adventure_non_overwrite_prunes_deleted_upstream_file(self) -> None:
        adv_path = self.target_dir / "lesovik-station"
        adv_path.mkdir(parents=True, exist_ok=True)
        deleted_upstream_file = adv_path / "lore" / "deleted_upstream.txt"
        deleted_upstream_file.parent.mkdir(parents=True, exist_ok=True)
        deleted_upstream_file.write_text("file deleted in upstream", encoding="utf-8")

        mock_meta = MagicMock()
        mock_meta.download_as_bytes.return_value = json.dumps({"title": "Lesovik"}).encode("utf-8")
        items = [(mock_meta, "metadata.json")]

        # Non-overwrite mode should still prune local files that were deleted upstream
        download_adventure_from_gcs(
            adventure_slug="lesovik-station",
            items=items,
            target_dir=self.target_dir,
            overwrite=False,
        )

        self.assertFalse(deleted_upstream_file.exists())
        self.assertTrue((adv_path / "metadata.json").exists())

    def test_remove_dir_tree_handles_readonly_file(self) -> None:
        import stat
        from scripts.download_adventures_from_gcs import remove_dir_tree

        test_dir = self.target_dir / "readonly_test"
        test_dir.mkdir(parents=True, exist_ok=True)
        ro_file = test_dir / "readonly.txt"
        ro_file.write_text("locked", encoding="utf-8")
        ro_file.chmod(stat.S_IREAD)

        remove_dir_tree(test_dir)
        self.assertFalse(test_dir.exists())


    def test_download_adventure_diff_skips_unchanged_files(self) -> None:
        from scripts.download_adventures_from_gcs import compute_file_md5

        adv_path = self.target_dir / "lesovik-station"
        adv_path.mkdir(parents=True, exist_ok=True)
        yaml_path = adv_path / "theater.yaml"
        yaml_path.write_text("agent:\n  style: mystery\n", encoding="utf-8")

        mock_yaml_blob = MagicMock()
        mock_yaml_blob.size = yaml_path.stat().st_size
        mock_yaml_blob.md5_hash = compute_file_md5(yaml_path)

        mock_new_blob = MagicMock()
        mock_new_blob.size = 50
        mock_new_blob.md5_hash = "different=="
        mock_new_blob.download_as_bytes.return_value = b"new file content"

        items = [
            (mock_yaml_blob, "theater.yaml"),
            (mock_new_blob, "new_file.txt"),
        ]

        result = download_adventure_from_gcs(
            adventure_slug="lesovik-station",
            items=items,
            target_dir=self.target_dir,
            diff=True,
            prune=True,
        )

        # Unchanged theater.yaml is skipped (saving bandwidth!)
        self.assertEqual(result["skipped_count"], 1)
        self.assertIn("theater.yaml", result["skipped_files"])
        # New file is downloaded
        self.assertEqual(result["files_count"], 1)
        self.assertIn("new_file.txt", result["downloaded_files"])


if __name__ == "__main__":
    unittest.main()
