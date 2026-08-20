import unittest
from pathlib import Path


class TestStickyNotesUI(unittest.TestCase):
    def test_canvas_template_contains_sticky_notes_button_and_elements(self):
        canvas_path = Path("templates/canvas.html")
        self.assertTrue(canvas_path.exists(), "canvas.html should exist")

        content = canvas_path.read_text(encoding="utf-8")

        self.assertIn('id="sticky-notes-toggle-btn"', content)
        self.assertIn('id="sticky-notes-loading"', content)
        self.assertIn('id="sticky-notes-list"', content)
        self.assertIn('id="sticky-notes-count-badge"', content)
        self.assertIn('initStickyNotesHandler', content)
        self.assertIn('updateCanvasStickyNotes', content)
        self.assertIn('bottom: 1.5rem', content)
        self.assertIn('left: 4.8rem', content)
        self.assertIn('display: none;', content)
        self.assertIn('z-index: 24', content)
        self.assertIn('z-index: 30', content)


if __name__ == "__main__":
    unittest.main()
