import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import object_registry
from api_server.app import app
from testing.ui.base import UITestCase


class TestAdventureGuideUI(UITestCase):
    def setUp(self) -> None:
        super().setUp()
        object_registry.FLAGS.allow_mock_payments = True
        object_registry.FLAGS.testing_use_local = True
        object_registry.theater_manager.base_dir = self.theaters_dir
        self._original_db_is_live = object_registry.db.is_live
        self._original_db_path = object_registry.db.db_path
        object_registry.db.is_live = False
        object_registry.db.db_path = self.workspace / "test_adv_guide.db"
        object_registry.db._init_db()

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

    def tearDown(self) -> None:
        object_registry.db.close()
        object_registry.db.is_live = self._original_db_is_live
        object_registry.db.db_path = self._original_db_path
        object_registry.FLAGS.allow_mock_payments = False
        object_registry.FLAGS.testing_use_local = False
        super().tearDown()

    def test_canvas_route_serves_adventure_guide_markup(self) -> None:
        response = self.client.get("/canvas?theater_id=test_adv_theater")
        self.assertEqual(response.status_code, 200)
        html = response.text

        self.assertIn('id="adventure-guide-modal"', html)
        self.assertIn('id="menu-item-adventure-guide"', html)
        self.assertIn('id="adventure-guide-ack-btn"', html)
        self.assertIn("Adventure Mode Guide", html)
        self.assertIn("never told the same way twice", html)

    def test_theater_endpoint_includes_is_adventure_mode_field(self) -> None:
        owner = object_registry.db.register_user("adv_owner", "adv-owner@example.com", "Password123")
        object_registry.db.add_user_credits(owner["id"], 50.0, 2.5)

        # 1. Create Adventure Mode Theater
        adv_meta = object_registry.theater_manager.create_theater(
            name="Adv Theater",
            theater_id="test_adv_theater",
            theater_config={
                "story_planning": {
                    "adventure_mode": True,
                }
            },
        )
        object_registry.db.record_deployment("test_adv_theater", owner["id"], adv_meta.join_key, cost=0.0)

        response = self.client.get("/api/theaters/test_adv_theater")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("metadata", data)
        self.assertTrue(data["metadata"].get("is_adventure_mode"))

        # 2. Create Regular Non-Adventure Mode Theater
        reg_meta = object_registry.theater_manager.create_theater(
            name="Reg Theater",
            theater_id="test_reg_theater",
            theater_config={
                "story_planning": {
                    "adventure_mode": False,
                }
            },
        )
        object_registry.db.record_deployment("test_reg_theater", owner["id"], reg_meta.join_key, cost=0.0)

        response2 = self.client.get("/api/theaters/test_reg_theater")
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        self.assertIn("metadata", data2)
        self.assertFalse(data2["metadata"].get("is_adventure_mode"))


if __name__ == "__main__":
    unittest.main()
