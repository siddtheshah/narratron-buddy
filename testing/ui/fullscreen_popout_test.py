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

    def test_canvas_contains_fullscreen_and_popout_controls(self) -> None:
        response = self.client.get("/canvas?theater_id=test_theater")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn('src="/static/generated/canvas/canvas.js"', html)
        self.assertIn('href="/static/css/canvas.css"', html)

        for element in (
            'id="fullscreen-btn"',
            'id="obs-theater-btn"',
            'id="fullscreen-icon"',
            'id="chat-side-toggle-btn"',
            'id="popout-expand-btn"',
        ):
            self.assertIn(element, html)

        for element in (
            'id="popout-window-btn"',
            'id="popped-out-placeholder"',
        ):
            self.assertNotIn(element, html)

    def test_popout_route_serves_chat_template(self) -> None:
        response = self.client.get("/popout?theater_id=test_theater")
        self.assertEqual(response.status_code, 200)
        html = response.text

        for element in (
            "Narratron Pop-out Chat",
            'id="dock-back-btn"',
            'id="chat-messages"',
            'id="popout-prompt-text"',
            "/static/generated/canvas/popout.js",
        ):
            self.assertIn(element, html)


