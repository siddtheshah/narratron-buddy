import unittest
from pathlib import Path

from testing.ui.canvas_source import read_ui_source


class TestSuggestionsUI(unittest.TestCase):
    def test_canvas_template_contains_suggestions_button_and_elements(self):
        canvas_path = Path("templates/canvas.html")
        self.assertTrue(canvas_path.exists(), "canvas.html should exist")

        content = read_ui_source(canvas_path)

        self.assertIn('id="suggestions-toggle-btn"', content)
        self.assertIn('id="suggestions-loading"', content)
        self.assertIn('id="suggestions-list"', content)
        self.assertIn('initSuggestionsHandler', content)
        self.assertIn('cachedFingerprint', content)
        self.assertIn('bottom: 1.5rem', content)
        self.assertIn('display: none;', content)



if __name__ == "__main__":
    unittest.main()

