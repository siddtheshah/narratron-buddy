"""Backend persistence coverage retained after the browser test migration."""

import json

from testing.ui.base import UITestCase


class TestDoodles(UITestCase):
    def test_doodles_persist_and_reset_when_cleared(self) -> None:
        theater_id = "doodle_persistence"
        first_image = self.workspace / "img1.jpg"
        second_image = self.workspace / "img2.jpg"
        first_image.write_bytes(b"image one")
        second_image.write_bytes(b"image two")
        manager = self.make_canvas_state(theater_id)
        manager.visual.show_image(str(first_image))

        first_doodle = {
            "type": "draw", "x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2,
            "color": "#ffffff", "size": 3,
        }
        second_doodle = {
            "type": "draw", "x0": 0.2, "y0": 0.2, "x1": 0.3, "y1": 0.3,
            "color": "#ff0000", "size": 5,
        }
        manager.doodles.add([first_doodle])
        manager.doodles.add([second_doodle])

        theater_file = self.theaters_dir / theater_id / "theater.json"
        saved = json.loads(theater_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["canvas_state"]["doodles"], [first_doodle, second_doodle])
        reloaded = self.make_canvas_state(theater_id)
        self.assertEqual(reloaded.doodles.doodles, [first_doodle, second_doodle])

        manager.visual.show_image(str(second_image))
        self.assertEqual(manager.doodles.doodles, [])
        manager.visual.previous_image()
        self.assertEqual(manager.doodles.doodles, [first_doodle, second_doodle])

        manager.doodles.add([{"type": "clear"}])
        self.assertEqual(manager.doodles.doodles, [])
        saved = json.loads(theater_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["canvas_state"]["doodles"], [])
        self.assertEqual(self.make_canvas_state(theater_id).doodles.doodles, [])
