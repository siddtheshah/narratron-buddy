from fastapi.testclient import TestClient
from unittest.mock import patch

from testing.ui.base import UITestCase
from api_server.app import app


class TestFullscreenPopout(UITestCase):
    def setUp(self):
        super().setUp()
        access_patcher = patch("api_server.app._require_canvas_access")
        access_patcher.start()
        self.addCleanup(access_patcher.stop)
        self.client = TestClient(app)

    def test_canvas_contains_fullscreen_and_popout_controls(self):
        response = self.client.get("/canvas?theater_id=test_theater")
        self.assertEqual(response.status_code, 200)
        html = response.text

        for element in (
            'id="fullscreen-btn"',
            'id="obs-theater-btn"',
            'id="fullscreen-icon"',
            "toggleFullScreen",
            "fullscreen-cinematic",
            "showCinematicUI",
            'id="popout-collapse-btn"',
            'id="popout-expand-btn"',
            'id="popout-window-btn"',
            'id="popped-out-placeholder"',
            "narratron_popout_channel",
            "openPopoutWindow",
        ):
            self.assertIn(element, html)

    def test_popout_route_serves_chat_template(self):
        response = self.client.get("/popout?theater_id=test_theater")
        self.assertEqual(response.status_code, 200)
        html = response.text

        for element in (
            "Narratron Pop-out Chat",
            'id="dock-back-btn"',
            'id="chat-messages"',
            'id="popout-prompt-text"',
            "narratron_popout_channel",
        ):
            self.assertIn(element, html)
