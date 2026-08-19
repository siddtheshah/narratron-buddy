"""Static wiring coverage for the shared browser auth-state cache."""

from pathlib import Path


def test_canvas_deduplicates_its_initial_auth_state_request():
    auth_flow = Path("static/js/auth-flow.js").read_text(encoding="utf-8")
    canvas = Path("templates/canvas.html").read_text(encoding="utf-8")

    assert "function getAuthState" in auth_flow
    assert "function invalidateAuthState" in auth_flow
    assert "authStatePromise" in auth_flow
    assert "function getCanvasAuthState" in canvas
    # One declaration plus the chat, baton, and microphone consumers.
    assert canvas.count("getCanvasAuthState()") == 4
    assert canvas.count("fetch('/api/auth/me')") == 1


def test_canvas_uses_dynamic_is_current_orator_check():
    canvas = Path("templates/canvas.html").read_text(encoding="utf-8")
    assert "function isCurrentOrator()" in canvas
    assert "isCurrentOrator() && agentWs" in canvas
    assert "if (!isCurrentOrator()) return;" in canvas

