"""Regression coverage for the orator canvas pin control."""

from pathlib import Path


def test_canvas_template_contains_orator_pin_control_and_state_sync():
    content = Path("templates/canvas.html").read_text(encoding="utf-8")

    assert 'id="canvas-pin-btn"' in content
    assert "body: JSON.stringify({ pinned: !canvasPinned })" in content
    assert "canvasPinned = Boolean(data.pinned)" in content
    assert "updateCanvasPinButton()" in content
