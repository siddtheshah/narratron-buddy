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


if __name__ == "__main__":
    unittest.main()
