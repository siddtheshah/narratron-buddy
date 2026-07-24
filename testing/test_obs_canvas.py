# -*- coding: utf-8 -*-
"""
test_obs_canvas.py — verifies dedicated OBS Studio Browser Source route (/obs) and query parameter support.
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


class TestOBSCanvas(BaseTestCase):
    def test_obs_route(self):
        """Verify /obs and /obs?session_id=... routes serve obs.html cleanly without UI chrome."""
        response = client.get("/obs?session_id=test_session")
        self.assertEqual(response.status_code, 200)
        html = response.text

        # Verify key OBS Canvas elements exist
        self.assertIn('Narratron OBS Canvas', html)
        self.assertIn('id="canvas-renderer"', html)
        self.assertIn('id="doodle-canvas"', html)
        self.assertIn('id="current-image"', html)

        # Verify UI Chrome elements are completely omitted
        self.assertNotIn('id="chat-sidebar"', html)
        self.assertNotIn('id="fullscreen-btn"', html)
        self.assertNotIn('id="bottom-bar"', html)
        self.assertNotIn('id="prompt-toggle-btn"', html)
        self.assertNotIn('id="history-paging-controls"', html)

    def test_obs_path_parameter_route(self):
        """Verify /obs/session_id path route functions properly."""
        response = client.get("/obs/test_session_123")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn('Narratron OBS Canvas', html)

    def test_canvas_obs_query_flag(self):
        """Verify /canvas?obs=1 query parameter activates OBS mode template."""
        response = client.get("/canvas?session_id=test_session&obs=1")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn('Narratron OBS Canvas', html)
        self.assertNotIn('id="chat-sidebar"', html)


if __name__ == "__main__":
    unittest.main()
