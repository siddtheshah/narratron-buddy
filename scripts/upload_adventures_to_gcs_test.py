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
    compute_file_md5,
    compute_file_crc32c,
    is_file_changed,
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

    def test_compute_file_md5_and_crc32c(self):
        sample_file = Path(self.temp_dir.name) / "test.txt"
        sample_file.write_text("hello world", encoding="utf-8")
        md5 = compute_file_md5(sample_file)
        self.assertEqual(md5, "XrY7u+Ae7tCTyyK7j1rNww==")

        crc = compute_file_crc32c(sample_file)
        self.assertEqual(crc, "yZRlqg==")

    def test_is_file_changed_scenarios(self):
        file_path = self.adv_dir / "theater.yaml"
        file_size = file_path.stat().st_size
        file_md5 = compute_file_md5(file_path)
        file_crc = compute_file_crc32c(file_path)

        # Case 1: remote_blob is None (new file)
        self.assertTrue(is_file_changed(file_path, None))

        # Case 2: size differs
        mock_blob = MagicMock()
        mock_blob.size = file_size + 10
        mock_blob.md5_hash = file_md5
        self.assertTrue(is_file_changed(file_path, mock_blob))

        # Case 3: size and MD5 match (identical)
        mock_blob.size = file_size
        mock_blob.md5_hash = file_md5
        mock_blob.crc32c = file_crc
        self.assertFalse(is_file_changed(file_path, mock_blob))

        # Case 4: size matches, but MD5 differs
        mock_blob.md5_hash = "different_md5=="
        self.assertTrue(is_file_changed(file_path, mock_blob))

        # Case 5: size matches, MD5 absent, CRC32c matches
        mock_blob.md5_hash = None
        mock_blob.crc32c = file_crc
        self.assertFalse(is_file_changed(file_path, mock_blob))

        # Case 6: size matches, MD5 absent, CRC32c differs
        mock_blob.md5_hash = None
        mock_blob.crc32c = "different_crc=="
        self.assertTrue(is_file_changed(file_path, mock_blob))

    def test_upload_adventure_diff_skips_identical_files(self):
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"

        # Ensure metadata.json has been normalized first
        create_or_load_metadata(self.adv_dir)

        # Create remote blobs with matching size and md5 for all 3 local files
        remote_blobs = []
        files = collect_adventure_files(self.adv_dir)
        for f in files:
            rel = f.relative_to(self.adv_dir).as_posix()
            b = MagicMock()
            b.name = f"adventures/lesovik-station/{rel}"
            b.size = f.stat().st_size
            b.md5_hash = compute_file_md5(f)
            b.crc32c = compute_file_crc32c(f)
            remote_blobs.append(b)

        mock_bucket.list_blobs.return_value = remote_blobs
        mock_target_blob = MagicMock()
        mock_bucket.blob.return_value = mock_target_blob

        result = upload_adventure_to_gcs(
            adventure_dir=self.adv_dir,
            bucket=mock_bucket,
            gcs_prefix="adventures",
            clear_existing=False,
            diff=True,
            prune=False,
            dry_run=False,
        )

        self.assertEqual(result["files_count"], 0)
        self.assertEqual(result["skipped_count"], 3)
        self.assertEqual(result["total_bytes"], 0)
        mock_target_blob.upload_from_filename.assert_not_called()

    def test_upload_adventure_diff_uploads_only_modified_and_new(self):
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"

        # Ensure metadata is normalized
        create_or_load_metadata(self.adv_dir)

        # Suppose theater.yaml is identical remotely,
        # metadata.json was modified (different md5),
        # and references/station.png is missing remotely (new file).
        yaml_file = self.adv_dir / "theater.yaml"
        b_yaml = MagicMock()
        b_yaml.name = "adventures/lesovik-station/theater.yaml"
        b_yaml.size = yaml_file.stat().st_size
        b_yaml.md5_hash = compute_file_md5(yaml_file)

        meta_file = self.adv_dir / "metadata.json"
        b_meta = MagicMock()
        b_meta.name = "adventures/lesovik-station/metadata.json"
        b_meta.size = meta_file.stat().st_size
        b_meta.md5_hash = "stale_hash=="

        mock_bucket.list_blobs.return_value = [b_yaml, b_meta]
        mock_target_blob = MagicMock()
        mock_bucket.blob.return_value = mock_target_blob

        result = upload_adventure_to_gcs(
            adventure_dir=self.adv_dir,
            bucket=mock_bucket,
            gcs_prefix="adventures",
            clear_existing=False,
            diff=True,
            prune=False,
            dry_run=False,
        )

        self.assertEqual(result["files_count"], 2)  # metadata.json + station.png
        self.assertEqual(result["skipped_count"], 1)  # theater.yaml skipped
        self.assertIn("metadata.json", result["uploaded_files"])
        self.assertIn("references/station.png", result["uploaded_files"])
        self.assertIn("theater.yaml", result["skipped_files"])
        self.assertEqual(mock_target_blob.upload_from_filename.call_count, 2)

    def test_upload_adventure_diff_prunes_orphans(self):
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"

        orphaned_blob = MagicMock()
        orphaned_blob.name = "adventures/lesovik-station/deprecated_lore.txt"

        mock_bucket.list_blobs.return_value = [orphaned_blob]
        mock_target_blob = MagicMock()
        mock_bucket.blob.return_value = mock_target_blob

        result = upload_adventure_to_gcs(
            adventure_dir=self.adv_dir,
            bucket=mock_bucket,
            gcs_prefix="adventures",
            clear_existing=False,
            diff=True,
            prune=True,
            dry_run=False,
        )

        self.assertEqual(result["pruned_count"], 1)
        orphaned_blob.delete.assert_called_once()

    def test_upload_adventure_no_diff_uploads_all(self):
        mock_bucket = MagicMock()
        mock_bucket.name = "test-bucket"

        # Ensure metadata is normalized
        create_or_load_metadata(self.adv_dir)

        # Even if all files match remotely, diff=False forces upload
        remote_blobs = []
        files = collect_adventure_files(self.adv_dir)
        for f in files:
            rel = f.relative_to(self.adv_dir).as_posix()
            b = MagicMock()
            b.name = f"adventures/lesovik-station/{rel}"
            b.size = f.stat().st_size
            b.md5_hash = compute_file_md5(f)
            remote_blobs.append(b)

        mock_bucket.list_blobs.return_value = remote_blobs
        mock_target_blob = MagicMock()
        mock_bucket.blob.return_value = mock_target_blob

        result = upload_adventure_to_gcs(
            adventure_dir=self.adv_dir,
            bucket=mock_bucket,
            gcs_prefix="adventures",
            clear_existing=False,
            diff=False,
            prune=False,
            dry_run=False,
        )

        self.assertEqual(result["files_count"], 3)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(mock_target_blob.upload_from_filename.call_count, 3)


if __name__ == "__main__":
    unittest.main()
