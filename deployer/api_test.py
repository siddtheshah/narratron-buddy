"""API tests for Theater Deployer, Join Splash, and Authentication endpoints in FastAPI application."""

import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from fastapi.testclient import TestClient

from testing.base import BaseTestCase
from web_viewer_app import app, local_deployer, db, FLAGS


class TestTheaterAPI(BaseTestCase):

    def setUp(self):
        super().setUp()
        FLAGS.allow_mock_payments = True
        FLAGS.testing_use_local_database = True
        self._original_db_is_live = db.is_live
        self._original_db_path = db.db_path
        self.test_dir = tempfile.mkdtemp()
        local_deployer.base_dir = Path(self.test_dir).resolve()
        local_deployer.base_dir.mkdir(parents=True, exist_ok=True)
        # The app module is already imported when these tests run, so switch its
        # shared manager to the local database selected by the testing flag.
        db.is_live = False
        db.db_path = Path(self.test_dir) / "test_api_deployer.db"
        db._init_db()
        self.client = TestClient(app)

    def tearDown(self):
        FLAGS.allow_mock_payments = False
        FLAGS.testing_use_local_database = False
        db.close()
        db.is_live = self._original_db_is_live
        db.db_path = self._original_db_path
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_root_and_join_serves_splash_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Join a Live Story Theater", response.text)
        self.assertIn("Narratron Buddy", response.text)

        join_response = self.client.get("/join")
        self.assertEqual(join_response.status_code, 200)
        self.assertIn("Join a Live Story Theater", join_response.text)
        self.assertIn('href="/about"', join_response.text)

    def test_deploy_page(self):
        response = self.client.get("/deploy")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Theater Deployer", response.text)
        self.assertIn("Deploy Canvas Instance", response.text)
        self.assertIn('href="/about"', response.text)

    def test_about_page_renders_about_markdown(self):
        response = self.client.get("/about")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Narratron Information Page", response.text)
        self.assertIn("Frequently Asked Questions", response.text)
        self.assertIn("About the (Original) Developer", response.text)
        self.assertIn("Sidd", response.text)

    def test_canvas_page(self):
        owner = db.register_user("canvas_owner", "canvas-owner@example.com", "Password123")
        self.assertTrue(db.record_deployment("test_theater", owner["id"], "KEY-CANVAS", cost=5.0))

        response = self.client.get("/canvas?theater_id=test_theater")
        self.assertEqual(response.status_code, 403)

        response = self.client.get(
            "/canvas?theater_id=test_theater&join_key=KEY-CANVAS",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertNotIn("join_key", response.headers["location"])

        response = self.client.get("/canvas?theater_id=test_theater")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Narratron Canvas", response.text)
        self.assertIn('href="/about"', response.text)

    def test_canvas_data_endpoints_require_canvas_access(self):
        owner = db.register_user("canvas_api_owner", "canvas-api-owner@example.com", "Password123")
        self.assertTrue(db.record_deployment("protected_theater", owner["id"], "KEY-PROTECTED", cost=5.0))

        self.assertEqual(
            self.client.get("/api/latest?theater_id=protected_theater").status_code,
            403,
        )
        self.client.get(
            "/canvas?theater_id=protected_theater&join_key=KEY-PROTECTED",
            follow_redirects=False,
        )
        self.assertEqual(
            self.client.get("/api/latest?theater_id=protected_theater").status_code,
            200,
        )

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

    def test_create_and_deploy_theater_with_join_key(self):
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
            "/api/theaters/create-and-deploy",
            data={"name": "Integration Test Theater", "style": "storybook watercolor"},
            files=[
                ("reference_files", ref_file),
                ("playlist_ambient", track_file),
            ],
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        theater_id = data["theater_id"]
        join_key = data["theater"]["join_key"]
        self.assertIsNotNone(theater_id)
        self.assertIsNotNone(join_key)
        self.assertEqual(
            (local_deployer._get_theater_dir(theater_id) / "style.txt").read_text(encoding="utf-8"),
            "storybook watercolor",
        )

        # Test resolving join key
        res_key = self.client.post("/api/theaters/resolve-join-key", json={"join_key": join_key})
        self.assertEqual(res_key.status_code, 200)
        self.assertEqual(res_key.json()["theater_id"], theater_id)

        # Test destroying theater by owner
        del_res = self.client.delete(f"/api/theaters/{theater_id}")
        self.assertEqual(del_res.status_code, 200)
        self.assertEqual(del_res.json()["status"], "ok")

    def test_export_assets_no_duplication(self):
        import zipfile
        from web_viewer_app import canvas_states

        # Register and log in
        reg_res = self.client.post("/api/auth/register", json={
            "username": "export_tester",
            "email": "export_tester@example.com",
            "password": "Password123"
        })
        self.assertEqual(reg_res.status_code, 200)

        # Create theater
        response = self.client.post(
            "/api/theaters/create-and-deploy",
            data={"name": "Export Test Theater"}
        )
        self.assertEqual(response.status_code, 200)
        theater_id = response.json()["theater_id"]

        theater_dir = local_deployer._get_theater_dir(theater_id)
        out_dir = theater_dir / "output"
        images_dir = out_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        # Create image inside output/images/
        img_path = images_dir / "scene_01.jpg"
        img_path.write_bytes(b"fake_jpeg_data")

        # Simulate update_shown_image and history addition
        cs = canvas_states.get(theater_id)
        cs.update_shown_image(str(img_path), theater_id=theater_id)

        # Also save theater to DB
        self.client.post(f"/api/theaters/{theater_id}/save")

        # Export assets
        export_res = self.client.get(f"/api/theaters/{theater_id}/export-assets")
        self.assertEqual(export_res.status_code, 200)

        zip_bytes = export_res.content
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            filenames = zf.namelist()
            # Extract image filenames (ignoring folder prefix)
            base_names = [Path(f).name for f in filenames if f.endswith((".jpg", ".png", ".jpeg", ".webp"))]
            self.assertIn("scene_01.jpg", base_names)
            # Ensure no image basename is duplicated in the ZIP export
            self.assertEqual(len(base_names), len(set(base_names)), f"Duplicate image entries found in ZIP: {base_names}")

    def test_password_reset_api_flow(self):
        # 1. Register user
        reg_res = self.client.post("/api/auth/register", json={
            "username": "pw_reset_user",
            "email": "pwreset@example.com",
            "password": "OldPassword123"
        })
        self.assertEqual(reg_res.status_code, 200)

        # 2. Request forgot password
        forgot_res = self.client.post("/api/auth/forgot-password", json={
            "username_or_email": "pwreset@example.com"
        })
        self.assertEqual(forgot_res.status_code, 200)
        data = forgot_res.json()
        self.assertEqual(data["status"], "ok")
        # reset_link must NOT be leaked in the API response
        self.assertNotIn("reset_link", data)

        # Extract token directly from DB (simulates receiving it via email)
        with db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT token FROM password_reset_tokens ORDER BY rowid DESC LIMIT 1"
            )
            token = cursor.fetchone()["token"]
        self.assertTrue(len(token) > 10)

        # 3. Validate token
        val_res = self.client.get(f"/api/auth/reset-password/validate?token={token}")
        self.assertEqual(val_res.status_code, 200)
        self.assertTrue(val_res.json()["valid"])
        self.assertEqual(val_res.json()["username"], "pw_reset_user")

        # 4. Reset password
        reset_res = self.client.post("/api/auth/reset-password", json={
            "token": token,
            "new_password": "BrandNewPassword123"
        })
        self.assertEqual(reset_res.status_code, 200)
        self.assertEqual(reset_res.json()["status"], "ok")

        # 5. Try login with old password -> 401
        old_login = self.client.post("/api/auth/login", json={
            "username_or_email": "pw_reset_user",
            "password": "OldPassword123"
        })
        self.assertEqual(old_login.status_code, 401)

        # 6. Try login with new password -> 200
        new_login = self.client.post("/api/auth/login", json={
            "username_or_email": "pw_reset_user",
            "password": "BrandNewPassword123"
        })
        self.assertEqual(new_login.status_code, 200)
        self.assertEqual(new_login.json()["user"]["username"], "pw_reset_user")

    def test_buy_credits_api_flow(self):
        # 1. Unauthenticated request -> 401
        unauth_res = self.client.post("/api/payments/buy-credits", json={"package_id": "starter"})
        self.assertEqual(unauth_res.status_code, 401)

        # 2. Register & log in
        reg_res = self.client.post("/api/auth/register", json={
            "username": "buyer_user",
            "email": "buyer@example.com",
            "password": "Password123"
        })
        self.assertEqual(reg_res.status_code, 200)

        # Initial balance (100.0)
        me_res = self.client.get("/api/auth/me")
        self.assertEqual(me_res.json()["user"]["credits"], 100.0)

        # 3. Invalid / missing card -> 400 Bad Request
        bad_card_res = self.client.post("/api/payments/buy-credits", json={
            "package_id": "pro",
            "card_number": "1234"
        })
        self.assertEqual(bad_card_res.status_code, 400)
        self.assertIn("Invalid credit card number format", bad_card_res.json()["detail"])

        # 4. Declined card test -> 400
        decline_card_res = self.client.post("/api/payments/buy-credits", json={
            "package_id": "pro",
            "card_number": "4000000000000002",
            "card_exp": "12/28",
            "card_cvc": "123"
        })
        self.assertEqual(decline_card_res.status_code, 400)
        self.assertIn("declined", decline_card_res.json()["detail"])

        # 5. Buy Pro package with valid card details
        buy_res = self.client.post("/api/payments/buy-credits", json={
            "package_id": "pro",
            "card_number": "4242424242424242",
            "card_exp": "12/28",
            "card_cvc": "123",
            "card_name": "Valid User"
        })
        self.assertEqual(buy_res.status_code, 200)
        buy_data = buy_res.json()
        self.assertEqual(buy_data["status"], "ok")
        self.assertEqual(buy_data["credits_added"], 200.0)
        self.assertEqual(buy_data["user"]["credits"], 300.0)

        # 6. Check payment history endpoint
        hist_res = self.client.get("/api/payments/history")
        self.assertEqual(hist_res.status_code, 200)
        hist_data = hist_res.json()
        self.assertEqual(hist_data["status"], "ok")
        self.assertEqual(len(hist_data["transactions"]), 1)
        self.assertEqual(hist_data["transactions"][0]["credits_added"], 200.0)
        self.assertEqual(hist_data["transactions"][0]["amount_usd"], 18.00)

        # 7. Buy custom one-off payment (150 credits for $15.00)
        custom_res = self.client.post("/api/payments/buy-credits", json={
            "custom_credits": 150.0,
            "custom_usd": 15.00,
            "card_number": "4242424242424242",
            "card_exp": "12/28",
            "card_cvc": "123"
        })
        self.assertEqual(custom_res.status_code, 200)
        custom_data = custom_res.json()
        self.assertEqual(custom_data["credits_added"], 150.0)
        # 8. Service unavailable when gateway unconfigured
        FLAGS.allow_mock_payments = False
        unavail_res = self.client.post("/api/payments/buy-credits", json={
            "package_id": "starter",
            "card_number": "4242424242424242",
            "card_exp": "12/28",
            "card_cvc": "123"
        })
        self.assertEqual(unavail_res.status_code, 503)
        self.assertEqual(unavail_res.json()["detail"], "Payment service unavailable")


if __name__ == "__main__":
    unittest.main()

