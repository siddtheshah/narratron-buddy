"""Tests for persisting and resetting canvas doodles."""

import json

from testing.ui.base import UITestCase


class TestDoodles(UITestCase):
    def test_doodles_persist_and_reset_when_the_image_changes(self):
        session_id = "doodle_persistence"
        first_image = self.workspace / "img1.jpg"
        second_image = self.workspace / "img2.jpg"
        first_image.write_bytes(b"image one")
        second_image.write_bytes(b"image two")
        manager = self.make_canvas_state(session_id)
        manager.update_shown_image(str(first_image))

        first_doodle = {
            "type": "draw", "x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2,
            "color": "#ffffff", "size": 3,
        }
        second_doodle = {
            "type": "draw", "x0": 0.2, "y0": 0.2, "x1": 0.3, "y1": 0.3,
            "color": "#ff0000", "size": 5,
        }
        manager.add_doodle(first_doodle)
        manager.add_doodle(second_doodle)

        session_file = self.sessions_dir / session_id / "session.json"
        with session_file.open(encoding="utf-8") as file:
            saved_doodles = json.load(file)["canvas_state"]["doodles"]
        self.assertEqual(saved_doodles, [first_doodle, second_doodle])

        reloaded_manager = self.make_canvas_state(session_id)
        self.assertEqual(reloaded_manager.doodles_state, [first_doodle, second_doodle])

        manager.update_shown_image(str(first_image))
        self.assertEqual(manager.doodles_state, [first_doodle, second_doodle])

        manager.update_shown_image(str(second_image))
        self.assertEqual(manager.doodles_state, [])
