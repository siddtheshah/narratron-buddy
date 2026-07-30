"""Unit tests for LocalDeployer theater creation, deployment, and destruction."""

from pathlib import Path
import shutil
import tempfile
import unittest

from testing.base import BaseTestCase
from deployer.deployer import LocalDeployer, TheaterMetadata, extract_asset_package


class TestLocalDeployer(BaseTestCase):

    def setUp(self):
        super().setUp()
        self.test_dir = tempfile.mkdtemp()
        self.deployer = LocalDeployer(base_theaters_dir=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_create_theater_with_mounted_assets(self):
        ref_files = [
            ("hero.png", b"fake_png_data"),
            ("map.jpg", b"fake_jpg_data"),
        ]
        playlists = {
            ("ambient"): [("track1.mp3", b"fake_mp3_1"), ("track2.mp3", b"fake_mp3_2")],
            ("combat"): [("battle.mp3", b"fake_mp3_3")],
        }
        config = {"affective_dialog": True, "proactivity": False}

        theater = self.deployer.create_theater(
            name="Fantasy Quest",
            theater_id="test_theater_001",
            reference_files=ref_files,
            playlists_data=playlists,
            theater_config=config,
            style="painterly fantasy illustrations",
        )

        self.assertEqual(theater.theater_id, "test_theater_001")
        self.assertEqual(theater.name, "Fantasy Quest")
        self.assertIn("hero.png", theater.mounted_references)
        self.assertIn("map.jpg", theater.mounted_references)
        self.assertEqual(len(theater.mounted_playlists["ambient"]), 2)
        self.assertEqual(len(theater.mounted_playlists["combat"]), 1)

        # Check physical directory files
        theater_path = Path(self.test_dir) / "test_theater_001"
        self.assertTrue((theater_path / "references" / "hero.png").exists())
        self.assertTrue((theater_path / "playlists" / "ambient" / "track1.mp3").exists())
        self.assertTrue((theater_path / "theater.json").exists())
        self.assertEqual((theater_path / "style.txt").read_text(encoding="utf-8"), "painterly fantasy illustrations")

    def test_deploy_and_list_theaters(self):
        s1 = self.deployer.create_theater(name="Theater 1", theater_id="s1")
        s2 = self.deployer.create_theater(name="Theater 2", theater_id="s2")

        theaters = self.deployer.list_theaters()
        self.assertEqual(len(theaters), 2)

        deployed_s1 = self.deployer.deploy_theater("s1")
        self.assertEqual(deployed_s1.status, "deployed")

        fetched_s1 = self.deployer.get_theater("s1")
        self.assertIsNotNone(fetched_s1)
        self.assertEqual(fetched_s1.status, "deployed")

    def test_stop_and_destroy_theater(self):
        self.deployer.create_theater(name="To Delete", theater_id="del_1")
        self.deployer.stop_theater("del_1")
        
        stopped = self.deployer.get_theater("del_1")
        self.assertEqual(stopped.status, "stopped")

    def test_extract_asset_package(self):
        import io
        import zipfile

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr("my_assets/references/subfolder/hero.png", b"fake_png")
            zf.writestr("my_assets/playlists/ambient/rain.mp3", b"fake_mp3")
            zf.writestr("my_assets/style.txt", "cyberpunk neon glow")

        zip_bytes = zip_buf.getvalue()
        refs, playlists, style = extract_asset_package(zip_bytes)

        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0][0], "my_assets/references/subfolder/hero.png")
        self.assertEqual(refs[0][1], b"fake_png")
        self.assertIn("ambient", playlists)
        self.assertEqual(playlists["ambient"][0][0], "rain.mp3")
        self.assertEqual(style, "cyberpunk neon glow")

    def test_extract_asset_package_exceeds_10mb_limit(self):
        oversized_bytes = b"0" * (10 * 1024 * 1024 + 100)
        with self.assertRaises(ValueError) as ctx:
            extract_asset_package(oversized_bytes)
        self.assertIn("10MB", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
