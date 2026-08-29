"""Tests for CanvasStateManager image history and paging payloads."""

from pathlib import Path

from testing.ui.base import UITestCase


class TestImageHistoryPaging(UITestCase):
    def test_paging_bar_is_at_the_top_and_reveals_story_log_inspector(self):
        canvas = Path("templates/canvas.html").read_text(encoding="utf-8")

        self.assertIn("#history-paging-controls {", canvas)
        self.assertIn("top: 1.25rem;", canvas)
        self.assertIn('id="story-log-inspector"', canvas)
        self.assertIn('id="story-log-toggle-btn"', canvas)
        self.assertIn('from "/static/js/story-log-inspector.js"', canvas)
        inspector = Path("static/js/story-log-inspector.js").read_text(encoding="utf-8")
        self.assertIn("initializeStoryLogInspector", inspector)

    def test_image_history_is_capped_at_100_entries(self):
        manager = self.make_canvas_state("history_cap")

        for index in range(105):
            manager.update_shown_image(f"/virtual/path/to/image_{index}.png")

        history = manager.get_latest_state()["history"]
        self.assertEqual(len(manager.shown_images_history), 100)
        self.assertEqual(len(history), 100)
        self.assertIn("image_5.png", history[0]["path"])
        self.assertIn("image_104.png", history[-1]["path"])

    def test_image_history_payload_contains_presentation_details(self):
        first_image = self.workspace / "scene1.png"
        second_image = self.workspace / "scene2.jpg"
        first_image.write_bytes(b"scene1_data")
        second_image.write_bytes(b"scene2_data")

        manager = self.make_canvas_state("history_payload")
        manager.update_shown_image(str(first_image), transition="fade")
        manager.update_shown_image(str(second_image), transition="crossfade")

        history = manager.get_latest_state()["history"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["path"], str(first_image))
        self.assertEqual(history[0]["transition"], "fade")
        self.assertEqual(history[0]["effect"], "gleam3")
        self.assertEqual(history[1]["path"], str(second_image))
        self.assertEqual(history[1]["transition"], "crossfade")
        self.assertEqual(history[1]["effect"], "gleam3")
        self.assertTrue(history[0]["url"])
        self.assertIn("prompt", history[0])

    def test_history_persists_in_the_test_theater_directory(self):
        theater_id = "history_persistence"
        manager = self.make_canvas_state(theater_id)
        manager.update_shown_image("/path/a.png", transition="fade")
        manager.update_shown_image("/path/b.png", transition="crossfade")

        manager.save_local_theater_data(theater_dir=self.theaters_dir / theater_id)

        reloaded_manager = self.make_canvas_state(theater_id)
        self.assertEqual(len(reloaded_manager.shown_images_history), 2)
