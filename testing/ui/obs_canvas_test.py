from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from api_server.app import app
from testing.ui.base import UITestCase


class TestOBSCanvas(UITestCase):
    def setUp(self) -> None:
        super().setUp()
        access_patcher = patch(
            "api_server.pages._require_canvas_access_async",
            new_callable=AsyncMock,
            return_value={"theater_id": "test_theater", "join_key": "JOIN"},
        )
        access_patcher.start()
        self.addCleanup(access_patcher.stop)
        self.client = TestClient(app)

    def test_canvas_and_obs_routes_serve_html(self) -> None:
        # OBS rendering and omitted controls are verified in obs-rendering_test.ts.
        for route in ("/canvas?theater_id=test_theater", "/obs?theater_id=test_theater", "/obs/test_theater_123"):
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"].split(";")[0], "text/html")
