from fastapi.testclient import TestClient

from testing.ui.base import UITestCase
from web_viewer_app import app


class TestFullscreenPopout(UITestCase):
    def setUp(self):
        super().setUp()
        self.client = TestClient(app)

    def test_canvas_contains_fullscreen_and_popout_controls(self):
        response = self.client.get("/canvas?session_id=test_session")
        self.assertEqual(response.status_code, 200)
        html = response.text

        for element in (
            'id="fullscreen-btn"',
            'id="obs-session-btn"',
            'id="fullscreen-icon"',
            "toggleFullScreen",
            "fullscreen-cinematic",
            "showCinematicUI",
            'id="popout-toggle-btn"',
            'id="popout-expand-btn"',
            'id="popout-window-btn"',
            'id="popped-out-placeholder"',
            "narratron_popout_channel",
            "openPopoutWindow",
        ):
            self.assertIn(element, html)

    def test_popout_route_serves_chat_template(self):
        response = self.client.get("/popout?session_id=test_session")
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
