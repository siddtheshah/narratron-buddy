# -*- coding: utf-8 -*-
"""
test_fullscreen_popout.py — verifies Full Screen button and Pop-out panel route / elements.
"""

import sys
from pathlib import Path
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from web_viewer_app import app

client = TestClient(app)

def test_canvas_fullscreen_and_popout_elements():
    """Verify /canvas HTML contains Full Screen button and Pop-out Panel elements."""
    response = client.get("/canvas?session_id=test_session")
    assert response.status_code == 200
    html = response.text

    # Fullscreen & Cinematic mode elements check
    assert 'id="fullscreen-btn"' in html
    assert 'id="fullscreen-icon"' in html
    assert 'toggleFullScreen' in html
    assert 'fullscreen-cinematic' in html
    assert 'showCinematicUI' in html

    # Pop-out panel elements check
    assert 'id="popout-toggle-btn"' in html
    assert 'id="popout-expand-btn"' in html
    assert 'id="popout-window-btn"' in html
    assert 'id="popped-out-placeholder"' in html
    assert 'narratron_popout_channel' in html
    assert 'openPopoutWindow' in html

def test_popout_route():
    """Verify /popout route serves popout.html template cleanly."""
    response = client.get("/popout?session_id=test_session")
    assert response.status_code == 200
    html = response.text

    assert 'Narratron Pop-out Chat' in html
    assert 'id="dock-back-btn"' in html
    assert 'id="chat-messages"' in html
    assert 'id="popout-prompt-text"' in html
    assert 'narratron_popout_channel' in html

if __name__ == "__main__":
    test_canvas_fullscreen_and_popout_elements()
    test_popout_route()
    print("ALL FULLSCREEN AND POPOUT PANEL TESTS PASSED!")
