"""Tests for the theater workspace boundary."""

import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from components.theater_manager import TheaterManager, extract_asset_package


class TestTheaterManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.manager = TheaterManager(base_theaters_dir=self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_deploy_stop_and_destroy_theater(self):
        theater = self.manager.create_theater(
            name="Fantasy Quest",
            theater_id="quest",
            reference_files=[("references/maps/hero.png", b"image")],
            playlists_data={"ambient": [("rain.mp3", b"audio")]},
            theater_config={"proactivity": False},
            style="painted fantasy",
        )

        theater_dir = Path(self.temp_dir.name) / "quest"
        self.assertEqual(theater.status, "created")
        self.assertTrue((theater_dir / "references" / "maps" / "hero.png").exists())
        self.assertTrue((theater_dir / "playlists" / "ambient" / "rain.mp3").exists())
        self.assertEqual((theater_dir / "style.txt").read_text(encoding="utf-8"), "painted fantasy")
        self.assertEqual(self.manager.deploy_theater("quest").status, "deployed")
        self.assertEqual(self.manager.stop_theater("quest").status, "stopped")
        self.assertTrue(self.manager.destroy_theater("quest"))
        self.assertFalse(theater_dir.exists())

    def test_asset_reads_do_not_create_a_missing_theater_workspace(self):
        self.assertEqual(self.manager.get_theater_references("missing"), [])
        self.assertEqual(self.manager.get_theater_playlists("missing"), {})
        self.assertFalse(self.manager.get_theater_dir("missing").exists())

    def test_extract_asset_package_groups_assets_and_rejects_oversize_input(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("assets/references/hero.png", b"image")
            zip_file.writestr("assets/playlists/ambient/rain.mp3", b"audio")
            zip_file.writestr("assets/style.txt", "neon noir")

        references, playlists, style = extract_asset_package(archive.getvalue())
        self.assertEqual(references, [("assets/references/hero.png", b"image")])
        self.assertEqual(playlists, {"ambient": [("rain.mp3", b"audio")]})
        self.assertEqual(style, "neon noir")
        with self.assertRaises(ValueError):
            extract_asset_package(b"0" * (10 * 1024 * 1024 + 1))
