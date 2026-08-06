"""API tests for Theater Deployer, Join Splash, and Authentication endpoints in FastAPI application."""

import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient

from testing.base import BaseTestCase
from api_server.app import app, theater_manager, db, FLAGS, canvas_states
import object_registry
import api_server.theaters as theaters
from utils.config_loader import get_theater_default_config
from unittest.mock import MagicMock, patch


class TestTheaterAPI(BaseTestCase):

    def setUp(self):
        super().setUp()
        FLAGS.allow_mock_payments = True
        FLAGS.testing_use_local_database = True
        self._original_db_is_live = db.is_live
        self._original_db_path = db.db_path
        self.test_dir = tempfile.mkdtemp()
        theater_manager.base_dir = Path(self.test_dir).resolve()
        theater_manager.base_dir.mkdir(parents=True, exist_ok=True)
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
        self.assertIn("Deploy Theater", response.text)
        self.assertIn('href="/about"', response.text)
        self.assertIn("pricingModal", response.text)
        self.assertIn("openPricingModal", response.text)

    def test_pricing_api_route(self):
        # Basic rates lookup
        response = self.client.get("/api/pricing")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("voice_credit_rate", data)
        self.assertIn("image_credit_rate", data)
        self.assertIn("storage_gb_monthly_rate", data)
        self.assertIn("storage_gb_daily_rate", data)
        self.assertIn("credits_per_usd", data)
        self.assertIn("usd_per_credit", data)
        self.assertEqual(data["credits_per_usd"], 20.0)

        # Calculation query params
        calc_res = self.client.get("/api/pricing?voice_minutes=30&images_created=10&gb_amount=2&days=30&usd_amount=10")
        self.assertEqual(calc_res.status_code, 200)
        calc_data = calc_res.json()
        self.assertIn("calculation", calc_data)
        calc = calc_data["calculation"]
        self.assertEqual(calc["usage_credits"], 40.0)
        self.assertAlmostEqual(calc["storage_credits"], 1.98, places=2)
        self.assertEqual(calc["usd_credits"], 200.0)

        # Negative query param validation
        bad_res = self.client.get("/api/pricing?voice_minutes=-5")
        self.assertEqual(bad_res.status_code, 400)
        self.assertIn("voice_minutes must be non-negative", bad_res.json()["detail"])

    def test_about_page_renders_about_markdown(self):
        response = self.client.get("/about")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Narratron Information Page", response.text)
        self.assertIn("Frequently Asked Questions", response.text)
        self.assertIn("About the (Original) Developer", response.text)
        self.assertIn("Sidd", response.text)

    def test_canvas_page(self):
        owner = db.register_user("canvas_owner", "canvas-owner@example.com", "Password123")
        db.add_user_credits(owner["id"], 50.0, 2.5)
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

    def test_canvas_data_endpoints_require_canvas_access(self):
        owner = db.register_user("canvas_api_owner", "canvas-api-owner@example.com", "Password123")
        db.add_user_credits(owner["id"], 50.0, 2.5)
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

    def test_theater_config_api_get_and_post(self):
        owner = db.register_user("config_owner", "config-owner@example.com", "Password123")
        db.add_user_credits(owner["id"], 50.0, 2.5)
        self.assertTrue(db.record_deployment("config_theater", owner["id"], "KEY-CONFIG", cost=5.0))

        # Access canvas to grant canvas_access cookie
        self.client.get(
            "/canvas?theater_id=config_theater&join_key=KEY-CONFIG",
            follow_redirects=False,
        )

        # 1. GET config
        get_res = self.client.get("/api/theaters/config_theater/config")
        self.assertEqual(get_res.status_code, 200)
        data = get_res.json()
        self.assertIn("config_yaml", data)
        self.assertEqual(data["theater_id"], "config_theater")

        # 2. POST invalid YAML
        bad_post = self.client.post(
            "/api/theaters/config_theater/config",
            json={"config_yaml": "invalid: yaml: : :"}
        )
        self.assertEqual(bad_post.status_code, 400)

        # 3. POST valid YAML
        new_yaml = "agent:\n  name: TestAgent\n  cooldown: 5\n"
        post_res = self.client.post(
            "/api/theaters/config_theater/config",
            json={"config_yaml": new_yaml}
        )
        self.assertEqual(post_res.status_code, 200)
        res_json = post_res.json()
        self.assertEqual(res_json["status"], "ok")
        self.assertIn("Restart your agent", res_json["message"])

        # 4. Verify file on disk
        yaml_disk_path = theater_manager.base_dir / "config_theater" / "theater.yaml"
        self.assertTrue(yaml_disk_path.exists())
        self.assertEqual(yaml_disk_path.read_text(encoding="utf-8"), new_yaml)

        # 5. Verify DB record update
        dep = db.get_deployment("config_theater")
        self.assertIsNotNone(dep)
        tc = dep.get("theater_config", {})
        if isinstance(tc, str):
            self.assertIn("TestAgent", tc)
        elif isinstance(tc, dict):
            self.assertEqual(tc.get("agent", {}).get("name"), "TestAgent")

        # 6. Test format-yaml endpoint
        fmt_valid = self.client.post("/api/theaters/format-yaml", json={"config_yaml": "agent:\n  name: FormatTest\n"})
        self.assertEqual(fmt_valid.status_code, 200)
        self.assertIn("formatted_yaml", fmt_valid.json())

        fmt_invalid = self.client.post("/api/theaters/format-yaml", json={"config_yaml": "invalid: yaml: :"})
        self.assertEqual(fmt_invalid.status_code, 400)

    def test_default_config_exposes_agent_defaults(self):
        response = self.client.get("/api/theaters/default-config")
        self.assertEqual(response.status_code, 200)
        config = yaml.safe_load(response.json()["config_yaml"])
        self.assertEqual(config["agent"]["style"], get_theater_default_config()["agent"]["style"])
        self.assertEqual(
            config["agent"]["special_instructions"],
            get_theater_default_config()["agent"]["special_instructions"],
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
            data={"name": "Integration Test Theater", "agent_style": "storybook watercolor", "agent_special_instructions": "Be concise."},
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
        theater_yaml = (theater_manager.theater(theater_id).directory() / "theater.yaml").read_text(encoding="utf-8")
        self.assertIn("style: storybook watercolor", theater_yaml)
        self.assertIn("special_instructions: Be concise.", theater_yaml)

        # Test resolving join key
        res_key = self.client.post("/api/theaters/resolve-join-key", json={"join_key": join_key})
        self.assertEqual(res_key.status_code, 200)
        self.assertEqual(res_key.json()["theater_id"], theater_id)

        # Test destroying theater by owner
        del_res = self.client.delete(f"/api/theaters/{theater_id}")
        self.assertEqual(del_res.status_code, 200)
        self.assertEqual(del_res.json()["status"], "ok")

    def test_advanced_yaml_is_canonical_over_quick_agent_fields(self):
        self.client.post("/api/auth/register", json={
            "username": "advanced_config_user",
            "email": "advanced-config@example.com",
            "password": "Password123",
        })
        response = self.client.post(
            "/api/theaters/create-and-deploy",
            data={
                "name": "Advanced Config Theater",
                "agent_style": "quick style",
                "agent_special_instructions": "quick instructions",
                "advanced_config_canonical": "true",
                "theater_config_yaml": (
                    "agent:\n"
                    "  style: advanced style\n"
                    "  special_instructions: advanced instructions\n"
                ),
            },
        )

        self.assertEqual(response.status_code, 200)
        theater_id = response.json()["theater_id"]
        config = yaml.safe_load(
            (theater_manager.theater(theater_id).directory() / "theater.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(config["agent"]["style"], "advanced style")
        self.assertEqual(config["agent"]["special_instructions"], "advanced instructions")

    def test_folder_upload_requires_theater_yaml(self):
        self.client.post("/api/auth/register", json={
            "username": "folder_config_user",
            "email": "folder-config@example.com",
            "password": "Password123",
        })
        response = self.client.post(
            "/api/theaters/create-and-deploy",
            data={"name": "Incomplete Folder", "creation_mode": "folder"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("theater.yaml", response.json()["detail"])

    def test_quick_deploy_adds_default_story_track(self):
        self.client.post("/api/auth/register", json={
            "username": "quick_deploy_user",
            "email": "quick-deploy@example.com",
            "password": "Password123",
        })
        response = self.client.post(
            "/api/theaters/create-and-deploy",
            data={"name": "Quick Theater", "creation_mode": "blank"},
        )
        self.assertEqual(response.status_code, 200)
        theater_id = response.json()["theater_id"]
        track = theater_manager.theater(theater_id).playlists_dir() / "default" / "new_story.mp3"
        self.assertTrue(track.is_file())

    def test_theater_output_route_uses_theater_bound_output_directory(self):
        reg_res = self.client.post("/api/auth/register", json={
            "username": "output_tester",
            "email": "output@example.com",
            "password": "Password123",
        })
        self.assertEqual(reg_res.status_code, 200)

        create_res = self.client.post(
            "/api/theaters/create-and-deploy",
            data={"name": "Output Route Test Theater"},
        )
        self.assertEqual(create_res.status_code, 200)
        theater_id = create_res.json()["theater_id"]
        output_file = theater_manager.theater(theater_id).output_dir() / "test-image.jpg"
        output_file.write_bytes(b"test-image")

        response = self.client.get(f"/theaters/{theater_id}/output/test-image.jpg")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"test-image")

    def test_theater_owner_can_toggle_viewer_collaboration(self):
        reg_res = self.client.post("/api/auth/register", json={
            "username": "collab_owner",
            "email": "collab@example.com",
            "password": "Password123",
        })
        self.assertEqual(reg_res.status_code, 200)

        create_res = self.client.post(
            "/api/theaters/create-and-deploy",
            data={"name": "Collaboration Test Theater"},
        )
        self.assertEqual(create_res.status_code, 200)
        theater_id = create_res.json()["theater_id"]

        response = self.client.post(
            f"/api/theaters/{theater_id}/collab",
            json={"enabled": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["viewer_collab_enabled"])
        self.assertTrue(canvas_states.get(theater_id).viewer_collab_enabled)

    def test_destroy_theater_not_on_disk(self):
        reg_res = self.client.post("/api/auth/register", json={
            "username": "db_delete_user",
            "email": "db_delete@example.com",
            "password": "Password123"
        })
        self.assertEqual(reg_res.status_code, 200)

        response = self.client.post(
            "/api/theaters/create-and-deploy",
            data={"name": "DB Only Theater", "style": "cyberpunk"},
        )
        self.assertEqual(response.status_code, 200)
        theater_id = response.json()["theater_id"]

        # Simulate theater not being on disk (e.g. not launched recently / cold server)
        import shutil
        t_dir = theater_manager.theater(theater_id).directory()
        if t_dir.exists():
            shutil.rmtree(t_dir)

        self.assertFalse(t_dir.exists())

        # Attempt deleting theater
        del_res = self.client.delete(f"/api/theaters/{theater_id}")
        self.assertEqual(del_res.status_code, 200)
        self.assertEqual(del_res.json()["status"], "ok")

        # Verify deployment is removed from DB
        self.assertIsNone(db.get_deployment(theater_id))

    def test_export_assets_no_duplication(self):
        import zipfile
        from api_server.app import canvas_states

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

        theater_dir = theater_manager.theater(theater_id).directory()
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

        # Initial balance (0.0)
        me_res = self.client.get("/api/auth/me")
        self.assertEqual(me_res.json()["user"]["credits"], 0.0)

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
        self.assertEqual(buy_data["credits_added"], 400.0)
        self.assertEqual(buy_data["user"]["credits"], 400.0)

        # 6. Check payment history endpoint
        hist_res = self.client.get("/api/payments/history")
        self.assertEqual(hist_res.status_code, 200)
        hist_data = hist_res.json()
        self.assertEqual(hist_data["status"], "ok")
        self.assertEqual(len(hist_data["transactions"]), 1)
        self.assertEqual(hist_data["transactions"][0]["credits_added"], 400.0)
        self.assertEqual(hist_data["transactions"][0]["amount_usd"], 18.00)

        # 8. Service unavailable when gateway unconfigured
        orig_key = os.environ.pop("STRIPE_SECRET_KEY", None)
        orig_local_db_flag = FLAGS.testing_use_local_database
        try:
            FLAGS.allow_mock_payments = False
            FLAGS.testing_use_local_database = False
            unavail_res = self.client.post("/api/payments/buy-credits", json={
                "package_id": "starter",
                "card_number": "4242424242424242",
                "card_exp": "12/28",
                "card_cvc": "123"
            })
            self.assertEqual(unavail_res.status_code, 503)
            self.assertEqual(unavail_res.json()["detail"], "Payment service unavailable")
        finally:
            FLAGS.testing_use_local_database = orig_local_db_flag
            if orig_key is not None:
                os.environ["STRIPE_SECRET_KEY"] = orig_key


if __name__ == "__main__":
    unittest.main()


def test_list_theaters_reads_disk_and_database_metadata_from_registry():
    metadata = MagicMock()
    metadata.theater_id = "disk-stage"
    metadata.model_dump.return_value = {"theater_id": "disk-stage", "join_key": "DISK"}
    manager = MagicMock()
    manager.list_theaters.return_value = [metadata]
    registry_db = MagicMock()
    registry_db.get_all_exported_theater_ids.return_value = ["disk-stage", "db-stage"]
    registry_db.get_theater_metadata_from_db.return_value = {"theater_id": "db-stage", "join_key": "DB"}
    registry_db.get_theaters_last_used.return_value = {"db-stage": "2026-01-01"}
    registry_db.get_deployment.side_effect = lambda theater_id: {"user_id": 1} if theater_id == "disk-stage" else {"user_id": 2}

    with patch.object(object_registry, "theater_manager", manager), patch.object(object_registry, "db", registry_db), patch.object(theaters, "get_current_user", return_value={"id": 1}):
        result = theaters.list_theaters(MagicMock())

    by_id = {item["theater_id"]: item for item in result}
    assert by_id["disk-stage"]["is_owner"] is True
    assert by_id["db-stage"]["join_key"] == "\U0001f512 Owner Only"


def test_resolve_join_key_uses_registry_database_and_grants_verified_access():
    registry_db = MagicMock()
    registry_db.get_theater_by_join_key.return_value = {"theater_id": "stage", "join_key": "JOIN", "user_id": 2}
    manager = MagicMock()
    metadata = type("Metadata", (), {"theater_id": "stage", "name": "Mock Theater"})()
    manager.get_theater.return_value = metadata
    response = MagicMock()
    with patch.object(object_registry, "db", registry_db), patch.object(object_registry, "theater_manager", manager), patch.object(theaters, "_grant_canvas_access") as grant:
        result = theaters.resolve_join_key(theaters.ResolveJoinKeyRequest(join_key="JOIN"), MagicMock(), response)
    assert result == {"status": "ok", "theater_id": "stage", "name": "Mock Theater", "user_id": 2}
    grant.assert_called_once_with(response, __import__("unittest.mock").mock.ANY, "stage", "JOIN")


@pytest.mark.asyncio
async def test_save_theater_requires_the_registry_deployment_owner():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 2}
    with patch.object(object_registry, "db", registry_db), patch.object(theaters, "get_current_user", return_value={"id": 3}), pytest.raises(HTTPException) as error:
        await theaters.save_theater_to_db("stage", MagicMock())
    assert error.value.status_code == 403
    registry_db.persist_canvas_theater_async.assert_not_called()


def test_export_theater_requires_the_registry_deployment_owner():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 2}
    with patch.object(object_registry, "db", registry_db), patch.object(theaters, "get_current_user", return_value={"id": 3}), pytest.raises(HTTPException) as error:
        theaters.export_theater_assets("stage", MagicMock())
    assert error.value.status_code == 403

