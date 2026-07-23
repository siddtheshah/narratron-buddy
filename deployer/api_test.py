"""API tests for Session Deployer endpoints in FastAPI application."""

import io
from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from web_viewer_app import app, local_deployer


class TestSessionAPI(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        local_deployer.base_dir = Path(self.test_dir).resolve()
        local_deployer.base_dir.mkdir(parents=True, exist_ok=True)
        self.client = TestClient(app)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_root_serves_session_creation_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Narratron App Deployer", response.text)
        self.assertIn("Deploy Canvas Instance", response.text)

    def test_canvas_page(self):
        response = self.client.get("/canvas?session_id=test_session")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Narratron Canvas", response.text)

    def test_create_and_deploy_session_api(self):
        ref_file = ("hero.png", io.BytesIO(b"fake_hero_bytes"), "image/png")
        track_file = ("ambient_01.mp3", io.BytesIO(b"fake_mp3_bytes"), "audio/mpeg")

        response = self.client.post(
            "/api/sessions/create-and-deploy",
            data={"name": "Integration Test Session"},
            files=[
                ("reference_files", ref_file),
                ("playlist_ambient", track_file),
            ],
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        session_id = data["session_id"]
        self.assertIsNotNone(session_id)

        # Test listing sessions
        list_res = self.client.get("/api/sessions")
        self.assertEqual(list_res.status_code, 200)
        sessions = list_res.json()
        self.assertTrue(any(s["session_id"] == session_id for s in sessions))

        # Test serving session mounted reference file
        ref_res = self.client.get(f"/sessions/{session_id}/references/hero.png")
        self.assertEqual(ref_res.status_code, 200)
        self.assertEqual(ref_res.content, b"fake_hero_bytes")

        # Test serving session mounted playlist track
        track_res = self.client.get(f"/sessions/{session_id}/playlists/ambient/ambient_01.mp3")
        self.assertEqual(track_res.status_code, 200)
        self.assertEqual(track_res.content, b"fake_mp3_bytes")

        # Test destroying session
        del_res = self.client.delete(f"/api/sessions/{session_id}")
        self.assertEqual(del_res.status_code, 200)
        self.assertEqual(del_res.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
