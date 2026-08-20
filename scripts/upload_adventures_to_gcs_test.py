"""Unit tests for scripts/upload_adventures_to_gcs.py."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from scripts.upload_adventures_to_gcs import (
    clear_exact_matching_adventure,
    slugify,
    create_or_load_metadata,
    collect_adventure_files,
    upload_adventure_to_gcs,
)


class TestUploadAdventuresToGCS(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.adv_dir = Path(self.temp_dir.name) / "Lesovik Station"
        self.adv_dir.mkdir(parents=True)

        (self.adv_dir / "theater.yaml").write_text("agent:\n  style: mystery\n", encoding="utf-8")
        (self.adv_dir / "metadata.json").write_text(
            json.dumps({
                "id": "lesovik-station",
                "title": "Lesovik Station: The Arctic Mystery",
                "description": "Arctic mystery.",
                "created_at": "2026-08-18T18:00:00Z",
            }),
            encoding="utf-8",
        )
        refs = self.adv_dir / "references"
        refs.mkdir()
        (refs / "station.png").write_bytes(b"fakepng")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_slugify(self):
        self.assertEqual(slugify("Lesovik Station"), "lesovik-station")
        self.assertEqual(slugify("The Witches of Muthren!"), "the-witches-of-muthren")
        self.assertEqual(slugify("Pantheon of Hearts: Dating the Divine"), "pantheon-of-hearts-dating-the-divine")

    def test_create_or_load_metadata(self):
        meta = create_or_load_metadata(self.adv_dir)
        self.assertEqual(meta["id"], "lesovik-station")
        self.assertEqual(meta["title"], "Lesovik Station: The Arctic Mystery")

    def test_create_or_load_metadata_generates_default(self):
        new_adv = Path(self.temp_dir.name) / "My Custom Quest"
        new_adv.mkdir(parents=True)
        meta = create_or_load_metadata(new_adv)
        self.assertEqual(meta["id"], "my-custom-quest")
        self.assertEqual(meta["title"], "My Custom Quest")
        self.assertEqual(meta["genre"], "Adventure")
        self.assertTrue((new_adv / "metadata.json").exists())

    def test_collect_adventure_files(self):
        files = collect_adventure_files(self.adv_dir)
        filenames = [f.name for f in files]
        self.assertIn("theater.yaml", filenames)
        self.assertIn("metadata.json", filenames)
        self.assertIn("station.png", filenames)

    def test_clear_exact_matching_adventure_success(self):
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"
        mock_blob1 = MagicMock()
        mock_blob1.name = "adventures/lesovik-station/metadata.json"
        mock_blob1.download_as_bytes.return_value = json.dumps({
            "id": "lesovik-station",
            "title": "Lesovik Station: The Arctic Mystery",
        }).encode("utf-8")

        mock_blob2 = MagicMock()
        mock_blob2.name = "adventures/lesovik-station/station.png"

        mock_bucket.list_blobs.return_value = [mock_blob1, mock_blob2]

        deleted_count = clear_exact_matching_adventure(
            bucket=mock_bucket,
            gcs_prefix="adventures",
            adventure_slug="lesovik-station",
            adventure_title="Lesovik Station: The Arctic Mystery",
            dry_run=False,
        )

        self.assertEqual(deleted_count, 2)
        mock_bucket.delete_blobs.assert_called_once()

    def test_clear_exact_matching_adventure_mismatched_metadata_aborts(self):
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"
        mock_blob1 = MagicMock()
        mock_blob1.name = "adventures/lesovik-station/metadata.json"
        # Existing metadata in GCS has completely different ID & title
        mock_blob1.download_as_bytes.return_value = json.dumps({
            "id": "completely-different-adventure",
            "title": "Unrelated Story",
        }).encode("utf-8")

        mock_bucket.list_blobs.return_value = [mock_blob1]

        deleted_count = clear_exact_matching_adventure(
            bucket=mock_bucket,
            gcs_prefix="adventures",
            adventure_slug="lesovik-station",
            adventure_title="Lesovik Station: The Arctic Mystery",
            dry_run=False,
        )

        # Must abort deletion because existing metadata does not match
        self.assertEqual(deleted_count, 0)
        mock_blob1.delete.assert_not_called()

    def test_clear_exact_matching_adventure_unsafe_slug_aborts(self):
        mock_bucket = MagicMock()
        for unsafe_slug in ["", "/", ".", "*", "a"]:
            deleted = clear_exact_matching_adventure(
                bucket=mock_bucket,
                gcs_prefix="adventures",
                adventure_slug=unsafe_slug,
                adventure_title="Test",
                dry_run=False,
            )
            self.assertEqual(deleted, 0)
            mock_bucket.list_blobs.assert_not_called()

    def test_clear_exact_matching_adventure_prefix_mismatch_aborts(self):
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"
        corrupt_blob = MagicMock()
        # Blob name escapes the expected exact target prefix
        corrupt_blob.name = "adventures/other-folder/file.txt"
        mock_bucket.list_blobs.return_value = [corrupt_blob]

        deleted = clear_exact_matching_adventure(
            bucket=mock_bucket,
            gcs_prefix="adventures",
            adventure_slug="lesovik-station",
            adventure_title="Lesovik Station",
            dry_run=False,
        )

        self.assertEqual(deleted, 0)
        corrupt_blob.delete.assert_not_called()

    def test_upload_adventure_with_clearing(self):
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"
        old_blob = MagicMock()
        old_blob.name = "adventures/lesovik-station/stale_file.txt"

        def mock_list_blobs(prefix=""):
            if prefix == "adventures/lesovik-station/":
                return [old_blob]
            return []

        mock_bucket.list_blobs.side_effect = mock_list_blobs

        mock_target_blob = MagicMock()
        mock_bucket.blob.return_value = mock_target_blob

        result = upload_adventure_to_gcs(
            adventure_dir=self.adv_dir,
            bucket=mock_bucket,
            gcs_prefix="adventures",
            clear_existing=True,
            dry_run=False,
        )

        self.assertEqual(result["id"], "lesovik-station")
        self.assertEqual(result["cleared_count"], 1)
        self.assertEqual(result["files_count"], 3)
        self.assertEqual(mock_target_blob.upload_from_filename.call_count, 3)

    def test_upload_adventure_dry_run(self):
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"
        old_blob = MagicMock()
        old_blob.name = "adventures/lesovik-station/stale_file.txt"

        def mock_list_blobs(prefix=""):
            if prefix == "adventures/lesovik-station/":
                return [old_blob]
            return []

        mock_bucket.list_blobs.side_effect = mock_list_blobs
        mock_target_blob = MagicMock()
        mock_bucket.blob.return_value = mock_target_blob

        result = upload_adventure_to_gcs(
            adventure_dir=self.adv_dir,
            bucket=mock_bucket,
            gcs_prefix="adventures",
            clear_existing=True,
            dry_run=True,
        )

        self.assertEqual(result["id"], "lesovik-station")
        self.assertEqual(result["cleared_count"], 1)
        self.assertEqual(result["files_count"], 3)
        old_blob.delete.assert_not_called()
        mock_target_blob.upload_from_filename.assert_not_called()

    def test_upload_adventure_without_clearing(self):
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"
        old_blob = MagicMock()
        old_blob.name = "adventures/lesovik-station/stale_file.txt"

        def mock_list_blobs(prefix=""):
            if prefix == "adventures/lesovik-station/":
                return [old_blob]
            return []

        mock_bucket.list_blobs.side_effect = mock_list_blobs
        mock_target_blob = MagicMock()
        mock_bucket.blob.return_value = mock_target_blob

        result = upload_adventure_to_gcs(
            adventure_dir=self.adv_dir,
            bucket=mock_bucket,
            gcs_prefix="adventures",
            clear_existing=False,
            dry_run=False,
        )

        self.assertEqual(result["id"], "lesovik-station")
        self.assertEqual(result["cleared_count"], 0)
        self.assertEqual(result["files_count"], 3)
        old_blob.delete.assert_not_called()

    def test_clear_existing_fallback_to_individual_delete(self):
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"
        mock_blob = MagicMock()
        mock_blob.name = "adventures/lesovik-station/old.txt"

        mock_bucket.list_blobs.return_value = [mock_blob]
        mock_bucket.delete_blobs.side_effect = Exception("Batch delete unsupported")

        deleted = clear_exact_matching_adventure(
            bucket=mock_bucket,
            gcs_prefix="adventures",
            adventure_slug="lesovik-station",
            dry_run=False,
        )

        self.assertEqual(deleted, 1)
        mock_blob.delete.assert_called_once()


if __name__ == "__main__":
    unittest.main()
