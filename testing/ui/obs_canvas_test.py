from fastapi.testclient import TestClient
from unittest.mock import patch

from testing.ui.base import UITestCase
from web_viewer_app import app


class TestOBSCanvas(UITestCase):
    def setUp(self):
        super().setUp()
        access_patcher = patch("web_viewer_app._require_canvas_access")
        access_patcher.start()
        self.addCleanup(access_patcher.stop)
        self.client = TestClient(app)

    def test_obs_route_omits_ui_chrome(self):
        response = self.client.get("/obs?theater_id=test_theater")
        self.assertEqual(response.status_code, 200)
        html = response.text

        for element in (
            'Narratron OBS Canvas',
            'id="canvas-renderer"',
            'id="doodle-canvas"',
            'id="current-image"',
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

    def test_obs_theater_path_route(self):
        response = self.client.get("/obs/test_theater_123")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Narratron OBS Canvas", response.text)

    def test_canvas_obs_query_flag_uses_obs_template(self):
        response = self.client.get("/canvas?theater_id=test_theater&obs=1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Narratron OBS Canvas", response.text)
        self.assertNotIn('id="chat-sidebar"', response.text)
