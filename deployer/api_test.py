"""API tests for Session Deployer, Join Splash, and Authentication endpoints in FastAPI application."""

import io
from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from web_viewer_app import app, local_deployer, db


class TestSessionAPI(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        local_deployer.base_dir = Path(self.test_dir).resolve()
        local_deployer.base_dir.mkdir(parents=True, exist_ok=True)
        db.db_path = Path(self.test_dir) / "test_api_deployer.db"
        db._init_db()
        self.client = TestClient(app)


    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_root_and_join_serves_splash_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Join a Live Story Session", response.text)
        self.assertIn("Narratron Buddy", response.text)

        join_response = self.client.get("/join")
        self.assertEqual(join_response.status_code, 200)
        self.assertIn("Join a Live Story Session", join_response.text)

    def test_deploy_page(self):
        response = self.client.get("/deploy")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Session Deployer", response.text)
        self.assertIn("Deploy Canvas Instance", response.text)

    def test_canvas_page(self):
        response = self.client.get("/canvas?session_id=test_session")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Narratron Canvas", response.text)

    def test_auth_registration_and_login_flow(self):
        # Register
        reg_res = self.client.post("/api/auth/register", json={
            "username": "api_user",
            "email": "api@example.com",
            "password": "Password123"
        })
        self.assertEqual(reg_res.status_code, 200)
        self.assertIn("auth_token", reg_res.cookies)

        # Check /api/auth/me
        me_res = self.client.get("/api/auth/me")
        self.assertEqual(me_res.status_code, 200)
        self.assertTrue(me_res.json()["authenticated"])
        self.assertEqual(me_res.json()["user"]["username"], "api_user")

    def test_create_and_deploy_session_with_join_key(self):
        # Register and log in
        reg_res = self.client.post("/api/auth/register", json={
            "username": "creator_user",
            "email": "creator@example.com",
            "password": "Password123"
        })
        self.assertEqual(reg_res.status_code, 200)

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
        join_key = data["session"]["join_key"]
        self.assertIsNotNone(session_id)
        self.assertIsNotNone(join_key)

        # Test resolving join key
        res_key = self.client.post("/api/sessions/resolve-join-key", json={"join_key": join_key})
        self.assertEqual(res_key.status_code, 200)
        self.assertEqual(res_key.json()["session_id"], session_id)

        # Test destroying session by owner
        del_res = self.client.delete(f"/api/sessions/{session_id}")
        self.assertEqual(del_res.status_code, 200)
        self.assertEqual(del_res.json()["status"], "ok")

    def test_export_assets_no_duplication(self):
        import zipfile
        from web_viewer_app import get_canvas_state

        # Register and log in
        reg_res = self.client.post("/api/auth/register", json={
            "username": "export_tester",
            "email": "export_tester@example.com",
            "password": "Password123"
        })
        self.assertEqual(reg_res.status_code, 200)

        # Create session
        response = self.client.post(
            "/api/sessions/create-and-deploy",
            data={"name": "Export Test Session"}
        )
        self.assertEqual(response.status_code, 200)
        session_id = response.json()["session_id"]

        session_dir = local_deployer._get_session_dir(session_id)
        out_dir = session_dir / "output"
        images_dir = out_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        # Create image inside output/images/
        img_path = images_dir / "scene_01.jpg"
        img_path.write_bytes(b"fake_jpeg_data")

        # Simulate update_shown_image and history addition
        cs = get_canvas_state(session_id)
        cs.update_shown_image(str(img_path), session_id=session_id)

        # Also save session to DB
        self.client.post(f"/api/sessions/{session_id}/save")

        # Export assets
        export_res = self.client.get(f"/api/sessions/{session_id}/export-assets")
        self.assertEqual(export_res.status_code, 200)

        zip_bytes = export_res.content
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            filenames = zf.namelist()
            # Extract image filenames (ignoring folder prefix)
            base_names = [Path(f).name for f in filenames if f.endswith((".jpg", ".png", ".jpeg", ".webp"))]
            self.assertIn("scene_01.jpg", base_names)
            # Ensure no image basename is duplicated in the ZIP export
            self.assertEqual(len(base_names), len(set(base_names)), f"Duplicate image entries found in ZIP: {base_names}")


if __name__ == "__main__":
    unittest.main()

