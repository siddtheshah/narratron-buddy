"""Regression coverage for the canvas low-compositing mode."""

from api_server.shared import PROJECT_ROOT


def test_minimal_mode_removes_nonessential_compositor_work() -> None:
    canvas = (PROJECT_ROOT / "templates" / "canvas.html").read_text(encoding="utf-8")

    assert "Minimal Mode" in canvas
    assert "minimal-mode" in canvas
    assert "imageEffectsEnabled: () => !minimalMode" in canvas
    assert "imageRenderer.setImageEffectsEnabled?.(!minimalMode)" in canvas
    assert "if (minimalMode) stopImageAnimation?.();" in canvas
    assert "Animated media defeats the point of Minimal Mode" in canvas
    assert "html.minimal-mode #bg-layer" in canvas
    assert "html.minimal-mode #image-container" in canvas
    assert "html.minimal-mode body *" in canvas
    assert "backdrop-filter: none !important" in canvas
    assert "animation: none !important" in canvas
