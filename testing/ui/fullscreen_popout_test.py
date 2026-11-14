from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from api_server.app import app
from testing.ui.base import UITestCase


class TestFullscreenPopout(UITestCase):
    def setUp(self) -> None:
        super().setUp()
        access_patcher = patch(
            "api_server.pages._require_canvas_access_async",
            new_callable=AsyncMock,
            return_value={"theater_id": "test_theater", "join_key": "JOIN"},
        )
        access_patcher.start()
        self.addCleanup(access_patcher.stop)
        popout_access_patcher = patch(
            "api_server.pages._require_canvas_access",
            return_value={"theater_id": "test_theater", "join_key": "JOIN"},
        )
        popout_access_patcher.start()
        self.addCleanup(popout_access_patcher.stop)
        self.client = TestClient(app)

    def test_canvas_and_popout_routes_serve_html(self) -> None:
        # Controls and interactions are covered by fullscreen_popout_test.ts
        # and popout_test.ts; Python verifies the authorized server routes.
        for route in ("/canvas", "/popout"):
            with self.subTest(route=route):
                response = self.client.get(f"{route}?theater_id=test_theater")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"].split(";")[0], "text/html")
