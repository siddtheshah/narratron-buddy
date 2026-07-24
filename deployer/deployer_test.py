"""Unit tests for LocalDeployer session creation, deployment, and destruction."""

from pathlib import Path
import shutil
import tempfile
import unittest

from testing.base_test import BaseTestCase
from deployer.deployer import LocalDeployer, SessionMetadata


class TestLocalDeployer(BaseTestCase):

    def setUp(self):
        super().setUp()
        self.test_dir = tempfile.mkdtemp()
        self.deployer = LocalDeployer(base_sessions_dir=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_create_session_with_mounted_assets(self):
        ref_files = [
            ("hero.png", b"fake_png_data"),
            ("map.jpg", b"fake_jpg_data"),
        ]
        playlists = {
            ("ambient"): [("track1.mp3", b"fake_mp3_1"), ("track2.mp3", b"fake_mp3_2")],
            ("combat"): [("battle.mp3", b"fake_mp3_3")],
        }
        config = {"affective_dialog": True, "proactivity": False}

        session = self.deployer.create_session(
            name="Fantasy Quest",
            reference_files=ref_files,
            playlists_data=playlists,
            session_config=config,
            session_id="test_session_001",
        )

        self.assertEqual(session.session_id, "test_session_001")
        self.assertEqual(session.name, "Fantasy Quest")
        self.assertIn("hero.png", session.mounted_references)
        self.assertIn("map.jpg", session.mounted_references)
        self.assertEqual(len(session.mounted_playlists["ambient"]), 2)
        self.assertEqual(len(session.mounted_playlists["combat"]), 1)

        # Check physical directory files
        session_path = Path(self.test_dir) / "test_session_001"
        self.assertTrue((session_path / "references" / "hero.png").exists())
        self.assertTrue((session_path / "playlists" / "ambient" / "track1.mp3").exists())
        self.assertTrue((session_path / "session.json").exists())

    def test_deploy_and_list_sessions(self):
        s1 = self.deployer.create_session(name="Session 1", session_id="s1")
        s2 = self.deployer.create_session(name="Session 2", session_id="s2")

        sessions = self.deployer.list_sessions()
        self.assertEqual(len(sessions), 2)

        deployed_s1 = self.deployer.deploy_session("s1")
        self.assertEqual(deployed_s1.status, "deployed")

        fetched_s1 = self.deployer.get_session("s1")
        self.assertIsNotNone(fetched_s1)
        self.assertEqual(fetched_s1.status, "deployed")

    def test_stop_and_destroy_session(self):
        self.deployer.create_session(name="To Delete", session_id="del_1")
        self.deployer.stop_session("del_1")
        
        stopped = self.deployer.get_session("del_1")
        self.assertEqual(stopped.status, "stopped")

        success = self.deployer.destroy_session("del_1")
        self.assertTrue(success)
        self.assertIsNone(self.deployer.get_session("del_1"))


if __name__ == "__main__":
    unittest.main()
