"""Tests for the theater workspace boundary."""

import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from absl.testing import flagsaver

from components.theater_manager import TheaterManager, extract_asset_package, get_theaters_root


class TestTheaterRootSelection(unittest.TestCase):
    def test_local_root_defaults_to_workspace_theaters_directory(self):
        self.assertEqual(
            get_theaters_root().resolve(),
            (Path(__file__).parent.parent / "theaters").resolve(),
        )

    @flagsaver.flagsaver(use_cloud_theater_storage=True)
    def test_cloud_root_uses_tmp_theaters(self):
        self.assertEqual(get_theaters_root(), Path("/mnt/storage/theaters"))


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
            theater_config={"agent": {"proactivity": False, "style": "painted fantasy"}},
        )

        theater_dir = Path(self.temp_dir.name) / "quest"
        self.assertEqual(theater.status, "created")
        self.assertTrue((theater_dir / "references" / "maps" / "hero.png").exists())
        self.assertTrue((theater_dir / "playlists" / "ambient" / "rain.mp3").exists())
        self.assertIn("style: painted fantasy", (theater_dir / "theater.yaml").read_text(encoding="utf-8"))
        self.assertEqual(self.manager.deploy_theater("quest").status, "deployed")
        self.assertEqual(self.manager.stop_theater("quest").status, "stopped")
        self.assertTrue(self.manager.destroy_theater("quest"))
        self.assertFalse(theater_dir.exists())

    def test_asset_reads_do_not_create_a_missing_theater_workspace(self):
        self.assertEqual(self.manager.get_theater_references("missing"), [])
        self.assertEqual(self.manager.get_theater_playlists("missing"), {})
        self.assertFalse(self.manager.theater("missing").directory().exists())

    def test_theater_binds_workspace_paths_and_lifecycle_operations(self):
        self.manager.create_theater(name="Bound Theater", theater_id="bound")
        theater = self.manager.theater("bound")

        self.assertEqual(theater.directory(), Path(self.temp_dir.name) / "bound")
        self.assertEqual(theater.references_dir(), theater.directory() / "references")
        self.assertEqual(theater.image_artifacts_dir(), theater.directory() / "output" / "artifacts" / "images")
        self.assertEqual(theater.music_artifacts_dir(), theater.directory() / "output" / "music")
        self.assertEqual(theater.metadata.name, "Bound Theater")
        self.assertEqual(theater.deploy().status, "deployed")
        self.assertTrue(theater.destroy())

    def test_get_theater_migrates_canvas_only_legacy_metadata(self):
        theater_dir = Path(self.temp_dir.name) / "legacy"
        theater_dir.mkdir()
        (theater_dir / "theater.json").write_text(
            '{"canvas_state": {"chat_messages": []}}', encoding="utf-8"
        )

        theater = self.manager.get_theater("legacy")

        self.assertEqual(theater.theater_id, "legacy")
        self.assertEqual(theater.name, "legacy")
        self.assertEqual(theater.canvas_state, {"chat_messages": []})
        persisted = (theater_dir / "theater.json").read_text(encoding="utf-8")
        self.assertIn('"theater_id": "legacy"', persisted)
        self.assertIn('"name": "legacy"', persisted)

    def test_extract_asset_package_groups_assets_and_rejects_oversize_input(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("assets/references/hero.png", b"image")
            zip_file.writestr("assets/playlists/ambient/rain.mp3", b"audio")
            zip_file.writestr("assets/lore/kingdom_history.txt", "The kingdom was founded by navigators.")
            zip_file.writestr("assets/lore/secret.pdf", b"not text")
            zip_file.writestr("assets/theater.yaml", "agent:\n  style: folder style\n")

        references, playlists, lore, theater_config_yaml = extract_asset_package(archive.getvalue())
        self.assertEqual(references, [("assets/references/hero.png", b"image")])
        self.assertEqual(playlists, {"ambient": [("rain.mp3", b"audio")]})
        self.assertEqual(lore, [("assets/lore/kingdom_history.txt", b"The kingdom was founded by navigators.")])
        self.assertEqual(theater_config_yaml, "agent:\n  style: folder style\n")
        with self.assertRaises(ValueError):
            extract_asset_package(b"0" * (10 * 1024 * 1024 + 1))

    def test_lore_documents_are_text_only_and_cannot_escape_lore_directory(self):
        self.manager.create_theater(
            name="Lore Theater",
            theater_id="lore",
            lore_files=[("lore/world/setting.txt", b"Floating cities." )],
        )

        self.assertEqual(self.manager.get_lore_documents("lore"), ["world/setting.txt"])
        self.assertEqual(self.manager.read_lore_document("lore", "world/setting.txt"), "Floating cities.")
        with self.assertRaises(ValueError):
            self.manager.read_lore_document("lore", "../theater.yaml")
        with self.assertRaises(ValueError):
            self.manager.create_theater(
                name="Invalid Lore",
                theater_id="invalid-lore",
                lore_files=[("lore/notes.md", b"Not accepted.")],
            )
