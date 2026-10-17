import unittest
from pathlib import Path


class TestHideCanvasTextUI(unittest.TestCase):
    def test_canvas_template_contains_hide_text_button_and_elements(self) -> None:
        canvas_path = Path("templates/canvas.html")
        self.assertTrue(canvas_path.exists(), "canvas.html should exist")

        content = canvas_path.read_text(encoding="utf-8")

        self.assertIn('id="hide-canvas-text-btn"', content)
        self.assertIn('class="hide-text-icon-visible"', content)
        self.assertIn('class="hide-text-icon-hidden"', content)
        self.assertIn('initHideCanvasTextHandler', content)
        self.assertIn('setCanvasTextHidden', content)
        self.assertIn('#hide-canvas-text-btn {', content)
        self.assertIn('#image-container.hide-canvas-text #scene-dialogue-overlay', content)
        self.assertIn('#image-container.hide-canvas-text #canvas-text-layer', content)
        self.assertIn('display: none !important;', content)
        self.assertIn('narratron-hide-canvas-text', content)

    def test_hide_text_button_pointer_and_drag_capture_exclusion(self) -> None:
        canvas_path = Path("templates/canvas.html")
        canvas_content = canvas_path.read_text(encoding="utf-8")
        renderer_path = Path("static/js/canvas-renderers.js")
        renderer_content = renderer_path.read_text(encoding="utf-8")

        # Hide text button stops pointerdown and mousedown propagation
        self.assertIn("btn.addEventListener('pointerdown', (e) => e.stopPropagation());", canvas_content)
        self.assertIn("btn.addEventListener('mousedown', (e) => e.stopPropagation());", canvas_content)

        # Canvas selection box pointerdown and drag listeners exclude hide canvas text button
        self.assertIn('event.target.closest("#hide-canvas-text-btn")', renderer_content)

    def test_hide_text_button_role_positioning(self) -> None:
        canvas_path = Path("templates/canvas.html")
        content = canvas_path.read_text(encoding="utf-8")

        self.assertIn("if (hideCanvasTextBtn) hideCanvasTextBtn.style.left = '8.1rem';", content)
        self.assertIn("if (hideCanvasTextBtn) hideCanvasTextBtn.style.left = '1.5rem';", content)
        self.assertIn("if (gameStateToggleBtn) gameStateToggleBtn.style.left = '11.4rem';", content)
        self.assertIn("if (gameStateToggleBtn) gameStateToggleBtn.style.left = '4.8rem';", content)

    def test_game_renderer_css_positioning(self) -> None:
        css_path = Path("static/css/game-renderer.css")
        self.assertTrue(css_path.exists(), "game-renderer.css should exist")
        css_content = css_path.read_text(encoding="utf-8")
        self.assertIn("left: 11.4rem;", css_content)


if __name__ == "__main__":
    unittest.main()
