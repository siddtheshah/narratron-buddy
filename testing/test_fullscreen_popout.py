# -*- coding: utf-8 -*-
"""
test_fullscreen_popout.py — verifies Full Screen button and Pop-out panel route / elements.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import unittest
from fastapi.testclient import TestClient
from testing.base_test import BaseTestCase
from web_viewer_app import app

client = TestClient(app)


class TestFullscreenPopout(BaseTestCase):
    def test_canvas_fullscreen_and_popout_elements(self):
        """Verify /canvas HTML contains Full Screen button and Pop-out Panel elements."""
        response = client.get("/canvas?session_id=test_session")
        self.assertEqual(response.status_code, 200)
        html = response.text

        # Fullscreen & Cinematic mode elements check
        self.assertIn('id="fullscreen-btn"', html)
        self.assertIn('id="fullscreen-icon"', html)
        self.assertIn('toggleFullScreen', html)
        self.assertIn('fullscreen-cinematic', html)
        self.assertIn('showCinematicUI', html)

        # Pop-out panel elements check
        self.assertIn('id="popout-toggle-btn"', html)
        self.assertIn('id="popout-expand-btn"', html)
        self.assertIn('id="popout-window-btn"', html)
        self.assertIn('id="popped-out-placeholder"', html)
        self.assertIn('narratron_popout_channel', html)
        self.assertIn('openPopoutWindow', html)

    def test_popout_route(self):
        """Verify /popout route serves popout.html template cleanly."""
        response = client.get("/popout?session_id=test_session")
        self.assertEqual(response.status_code, 200)
        html = response.text

        self.assertIn('Narratron Pop-out Chat', html)
        self.assertIn('id="dock-back-btn"', html)
        self.assertIn('id="chat-messages"', html)
        self.assertIn('id="popout-prompt-text"', html)
        self.assertIn('narratron_popout_channel', html)


if __name__ == "__main__":
    unittest.main()
