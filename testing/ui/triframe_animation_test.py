"""UI-facing regression coverage for tri-frame animations without a live provider."""

from pathlib import Path

from PIL import Image

from api_server.shared import PROJECT_ROOT
from testing.ui.base import UITestCase


class TestTriFrameAnimationUI(UITestCase):
    def test_dummy_frames_are_exposed_and_wired_to_canvas_renderers(self):
        """Exercise the exact nested output paths the browser receives."""
        theater_id = "dummy_triframe"
        manager = self.make_canvas_state(theater_id)
        animation_dir = manager.theater.output_dir() / "animations" / "lantern_loop_123"
        animation_dir.mkdir(parents=True)
        colors = ("#b91c1c", "#15803d", "#1d4ed8")
        frame_paths = []
        for number, color in enumerate(colors, start=1):
            frame_path = animation_dir / f"frame_{number}.jpg"
            Image.new("RGB", (16, 16), color).save(frame_path, "JPEG")
            frame_paths.append(str(frame_path))

        manager.show_triframe(frame_paths)
        state = manager.get_latest_state()

        self.assertEqual(state["animation"]["type"], "triframe")
        self.assertEqual(state["animation"]["crossfade_duration_ms"], 500)
        self.assertEqual(state["animation"]["frames"], [
            f"/theaters/{theater_id}/output/animations/lantern_loop_123/frame_{number}.jpg"
            for number in range(1, 4)
        ])
        self.assertTrue(all(Path(path).is_file() for path in frame_paths))

        renderer = (PROJECT_ROOT / "static" / "js" / "canvas-renderers.js").read_text(encoding="utf-8")
        canvas = (PROJECT_ROOT / "templates" / "canvas.html").read_text(encoding="utf-8")
        obs = (PROJECT_ROOT / "templates" / "obs.html").read_text(encoding="utf-8")
        self.assertIn("playSequence", renderer)
        self.assertIn("playLayeredAnimation", renderer)
        self.assertIn("crossfadeDuration", renderer)
        self.assertIn('image.style.opacity = "0"', renderer)
        self.assertIn('backgroundLayer.style.backgroundImage = "none"', renderer)
        self.assertIn("playTriFrameAnimation(animation)", canvas)
        self.assertIn("playLayeredAnimation", canvas)
        self.assertIn("playImageSequence(data.animation.frames", obs)
        self.assertIn("playLayeredAnimation", obs)
