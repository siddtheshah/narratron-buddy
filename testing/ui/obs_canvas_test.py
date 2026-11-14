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

    def test_obs_route_omits_ui_chrome(self) -> None:
        response = self.client.get("/obs?theater_id=test_theater")
        self.assertEqual(response.status_code, 200)
        html = response.text

        for element in (
            "Narratron OBS Canvas",
            'id="canvas-renderer"',
            'id="doodle-canvas"',
            'id="current-image"',
            'import { initObsCanvas } from "/static/generated/canvas/obs.js";',
        ):
            self.assertIn(element, html)
        for element in (
            'id="chat-sidebar"',
            'id="fullscreen-btn"',
            'id="bottom-bar"',
            'id="prompt-toggle-btn"',
            'id="history-paging-controls"',
        ):
            self.assertNotIn(element, html)

    def test_obs_theater_path_route(self) -> None:
        response = self.client.get("/obs/test_theater_123")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Narratron OBS Canvas", response.text)

    def test_canvas_includes_disconnected_indicator(self) -> None:
        response = self.client.get("/canvas?theater_id=test_theater")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="agent-disconnected-indicator"', response.text)
        self.assertIn('href="/static/css/canvas.css"', response.text)

    def test_obs_canvas_includes_narration_pane(self) -> None:
        response = self.client.get("/obs?theater_id=test_theater")
        self.assertEqual(response.status_code, 200)
        html = response.text

        self.assertIn('id="narration-pane-mount"', html)
        self.assertIn("/static/css/narration-pane.css", html)
        self.assertIn('import { initObsCanvas } from "/static/generated/canvas/obs.js";', html)
        self.assertIn(".scene-description", html)

