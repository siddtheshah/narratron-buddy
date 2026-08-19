"""Unit tests for services/adventure_service.py."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from services.adventure_service import AdventureService


class TestAdventureService(unittest.TestCase):
    """Tests for AdventureService listing, sorting, cover loading, and asset extraction."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.local_dir = Path(self.temp_dir.name)

        # Create two sample adventures locally
        self.adv1_dir = self.local_dir / "Alpha Adventure"
        self.adv1_dir.mkdir(parents=True)
        (self.adv1_dir / "metadata.json").write_text(
            json.dumps({
                "id": "alpha-adventure",
                "title": "Alpha Adventure",
                "description": "First alpha test.",
                "created_at": "2026-08-10T10:00:00Z",
                "cover_image": "references/cover.png",
                "tags": ["Sci-Fi", "Action"],
            }),
            encoding="utf-8",
        )
        (self.adv1_dir / "theater.yaml").write_text("agent:\n  style: epic\n", encoding="utf-8")
        refs_dir = self.adv1_dir / "references"
        refs_dir.mkdir()
        (refs_dir / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\nfakeimage")
        pl_dir = self.adv1_dir / "playlists" / "battle"
        pl_dir.mkdir(parents=True)
        (pl_dir / "theme.mp3").write_bytes(b"ID3fakeaudio")
        lore_dir = self.adv1_dir / "lore"
        lore_dir.mkdir()
        (lore_dir / "history.txt").write_text("Lore history here", encoding="utf-8")

        self.adv2_dir = self.local_dir / "Beta Quest"
        self.adv2_dir.mkdir(parents=True)
        (self.adv2_dir / "metadata.json").write_text(
            json.dumps({
                "id": "beta-quest",
                "title": "Beta Quest",
                "description": "Second beta test.",
                "created_at": "2026-08-18T12:00:00Z",  # Newer than alpha
                "cover_image": "references/beta_cover.jpg",
                "tags": ["Fantasy"],
            }),
            encoding="utf-8",
        )
        (self.adv2_dir / "theater.yaml").write_text("agent:\n  style: mysterious\n", encoding="utf-8")
        refs2_dir = self.adv2_dir / "references"
        refs2_dir.mkdir()
        (refs2_dir / "beta_cover.jpg").write_bytes(b"\xff\xd8\xfffakejpeg")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_list_adventures_local_sorted_newest_first(self):
        service = AdventureService(
            bucket_name="test-bucket",
            local_fallback_dir=self.local_dir,
            storage_client=MagicMock(side_effect=Exception("No GCS")),
        )
        # Force local fallback by simulating empty GCS
        with patch.object(service, "_fetch_adventures_from_gcs", return_value=[]):
            adventures = service.list_adventures(force_refresh=True)

            self.assertEqual(len(adventures), 2)
            # Newest first: Beta Quest (2026-08-18) should come before Alpha Adventure (2026-08-10)
            self.assertEqual(adventures[0]["id"], "beta-quest")
            self.assertEqual(adventures[1]["id"], "alpha-adventure")
            self.assertEqual(adventures[0]["track_count"], 0)
            self.assertEqual(adventures[1]["track_count"], 1)
            self.assertEqual(adventures[1]["lore_count"], 1)
            self.assertEqual(adventures[1]["reference_count"], 1)

    def test_get_adventure_and_cover_local(self):
        service = AdventureService(
            bucket_name="test-bucket",
            local_fallback_dir=self.local_dir,
        )
        with patch.object(service, "_fetch_adventures_from_gcs", return_value=[]):
            adv = service.get_adventure("alpha-adventure")
            self.assertIsNotNone(adv)
            self.assertEqual(adv["title"], "Alpha Adventure")

            cover_data, ctype = service.get_adventure_cover("alpha-adventure")
            self.assertIsNotNone(cover_data)
            self.assertIn("png", ctype.lower())

    def test_load_adventure_assets_local(self):
        service = AdventureService(
            bucket_name="test-bucket",
            local_fallback_dir=self.local_dir,
        )
        with patch.object(service, "_get_bucket", return_value=None):
            refs, playlists, lore, config = service.load_adventure_assets("alpha-adventure")

            self.assertEqual(len(refs), 1)
            self.assertEqual(refs[0][0].replace("\\", "/"), "references/cover.png")
            self.assertIn("battle", playlists)
            self.assertEqual(len(playlists["battle"]), 1)
            self.assertEqual(playlists["battle"][0][0], "theme.mp3")
            self.assertEqual(len(lore), 1)
            self.assertEqual(lore[0][0].replace("\\", "/"), "lore/history.txt")
            self.assertIn("agent", config)
            self.assertEqual(config["agent"]["style"], "epic")

    def test_gcs_blob_listing_and_asset_loading(self):
        mock_client = MagicMock()
        mock_bucket = MagicMock()
        mock_client.bucket.return_value = mock_bucket

        # Mock blob items in GCS
        def make_mock_blob(name, data, content_type="application/octet-stream"):
            blob = MagicMock()
            blob.name = name
            blob.download_as_bytes.return_value = data
            blob.content_type = content_type
            blob.exists.return_value = True
            return blob

        meta_json = json.dumps({
            "id": "gcs-adventure",
            "title": "GCS Space Odyssey",
            "created_at": "2026-08-19T00:00:00Z",
            "cover_image": "references/space.jpg",
            "tags": ["Space"],
        }).encode("utf-8")

        mock_blobs = [
            make_mock_blob("adventures/gcs-adventure/metadata.json", meta_json, "application/json"),
            make_mock_blob("adventures/gcs-adventure/theater.yaml", b"agent:\n  style: cosmic\n", "text/yaml"),
            make_mock_blob("adventures/gcs-adventure/references/space.jpg", b"fakejpg", "image/jpeg"),
            make_mock_blob("adventures/gcs-adventure/playlists/ambient/drift.mp3", b"fakemp3", "audio/mpeg"),
            make_mock_blob("adventures/gcs-adventure/lore/log.txt", b"Captain's log", "text/plain"),
        ]

        mock_bucket.list_blobs.return_value = mock_blobs
        mock_bucket.blob.side_effect = lambda name: next((b for b in mock_blobs if b.name == name), make_mock_blob(name, b""))

        service = AdventureService(
            bucket_name="narratron-buddy-app-storage",
            prefix="adventures",
            storage_client=mock_client,
        )

        adventures = service.list_adventures(force_refresh=True)
        self.assertEqual(len(adventures), 1)
        self.assertEqual(adventures[0]["id"], "gcs-adventure")
        self.assertEqual(adventures[0]["track_count"], 1)
        self.assertEqual(adventures[0]["lore_count"], 1)
        self.assertEqual(adventures[0]["reference_count"], 1)

        refs, playlists, lore, config = service.load_adventure_assets("gcs-adventure")
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0][0], "references/space.jpg")
        self.assertIn("ambient", playlists)
        self.assertEqual(len(lore), 1)
        self.assertEqual(config["agent"]["style"], "cosmic")


if __name__ == "__main__":
    unittest.main()
