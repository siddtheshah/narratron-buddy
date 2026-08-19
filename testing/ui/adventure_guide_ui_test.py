from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from api_server.app import app, db, theater_manager, FLAGS
from testing.ui.base import UITestCase


class TestAdventureGuideUI(UITestCase):
    def setUp(self):
        super().setUp()
        FLAGS.allow_mock_payments = True
        FLAGS.testing_use_local_database = True
        theater_manager.base_dir = self.theaters_dir
        self._original_db_is_live = db.is_live
        self._original_db_path = db.db_path
        db.is_live = False
        db.db_path = self.workspace / "test_adv_guide.db"
        db._init_db()

        pages_access_patcher = patch(
            "api_server.pages._require_canvas_access_async",
            new_callable=AsyncMock,
            return_value={"theater_id": "test_adv_theater", "join_key": "JOIN"},
        )
        pages_access_patcher.start()
        self.addCleanup(pages_access_patcher.stop)

        theaters_access_patcher = patch(
            "api_server.theaters._require_canvas_access_async",
            new_callable=AsyncMock,
            return_value={"theater_id": "test_adv_theater", "join_key": "JOIN", "user_id": 1},
        )
        theaters_access_patcher.start()
        self.addCleanup(theaters_access_patcher.stop)

        self.client = TestClient(app)

    def tearDown(self):
        db.close()
        db.is_live = self._original_db_is_live
        db.db_path = self._original_db_path
        FLAGS.allow_mock_payments = False
        FLAGS.testing_use_local_database = False
        super().tearDown()

    def test_canvas_template_contains_adventure_guide_elements(self):
        canvas_path = Path("templates/canvas.html")
        self.assertTrue(canvas_path.exists(), "canvas.html should exist")

        content = canvas_path.read_text(encoding="utf-8")

        # Modal & Pullout Menu Elements
        self.assertIn('id="adventure-guide-modal"', content)
        self.assertIn('id="menu-item-adventure-guide"', content)
        self.assertIn('id="adventure-guide-close-btn"', content)
        self.assertIn('id="adventure-guide-ack-btn"', content)
        self.assertIn("Adventure Mode Guide", content)

        # Ensure not crammed into chat panel
        self.assertNotIn('id="adventure-guide-card"', content)

        # Key guidance copy
        self.assertIn("pre-existing sandbox and structure", content)
        self.assertIn("never told the same way twice", content)
        self.assertIn("Play In-Character, Don't Force Control", content)
        self.assertIn("disable adventure mode and take full control", content)

        # JS functions & logic
        self.assertIn("openAdventureGuideModal", content)
        self.assertIn("closeAdventureGuideModal", content)
        self.assertIn("initAdventureGuide", content)
        self.assertIn("narratron_adventure_guide_seen", content)

    def test_canvas_route_serves_adventure_guide_markup(self):
        response = self.client.get("/canvas?theater_id=test_adv_theater")
        self.assertEqual(response.status_code, 200)
        html = response.text

        self.assertIn('id="adventure-guide-modal"', html)
        self.assertIn('id="menu-item-adventure-guide"', html)
        self.assertIn('id="adventure-guide-ack-btn"', html)
        self.assertIn("Adventure Mode Guide", html)
        self.assertIn("never told the same way twice", html)

    def test_theater_endpoint_includes_is_adventure_mode_field(self):
        owner = db.register_user("adv_owner", "adv-owner@example.com", "Password123")
        db.add_user_credits(owner["id"], 50.0, 2.5)

        # 1. Create Adventure Mode Theater
        adv_meta = theater_manager.create_theater(
            name="Adv Theater",
            theater_id="test_adv_theater",
            theater_config={
                "story_planning": {
                    "adventure_mode": True,
                }
            },
        )
        db.record_deployment("test_adv_theater", owner["id"], adv_meta.join_key, cost=0.0)

        response = self.client.get("/api/theaters/test_adv_theater")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("metadata", data)
        self.assertTrue(data["metadata"].get("is_adventure_mode"))

        # 2. Create Regular Non-Adventure Mode Theater
        reg_meta = theater_manager.create_theater(
            name="Reg Theater",
            theater_id="test_reg_theater",
            theater_config={
                "story_planning": {
                    "adventure_mode": False,
                }
            },
        )
        db.record_deployment("test_reg_theater", owner["id"], reg_meta.join_key, cost=0.0)

        response2 = self.client.get("/api/theaters/test_reg_theater")
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        self.assertIn("metadata", data2)
        self.assertFalse(data2["metadata"].get("is_adventure_mode"))


if __name__ == "__main__":
    unittest.main()
