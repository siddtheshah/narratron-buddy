"""UI-facing regression coverage for video animation playback in the canvas."""

from pathlib import Path

from api_server.shared import PROJECT_ROOT
from testing.ui.base import UITestCase


class TestVideoAnimationUI(UITestCase):
    def test_video_animation_is_exposed_silent_and_looping_in_canvas_renderers(self):
        """Verify video animation state exposes loop/muted flags and canvas-renderers wires them."""
        theater_id = "dummy_video_theater"
        manager = self.make_canvas_state(theater_id)
        anim_dir = manager.theater.output_dir() / "animations" / "flight_123"
        anim_dir.mkdir(parents=True)
        fake_video_file = anim_dir / "video.mp4"
        fake_video_file.write_bytes(b"\x00\x00\x00 fake mp4 data")

        manifest = {
            "id": "flight_123",
            "type": "video",
            "scene_prompt": "A soaring griffin over snow-capped mountains.",
            "video_path": str(fake_video_file),
            "video_url": f"/theaters/{theater_id}/output/animations/flight_123/video.mp4",
            "loop": True,
            "muted": True,
        }
        manager.show_video_animation(manifest)
        state = manager.get_latest_state()

        self.assertEqual(state["animation"]["type"], "video")
        self.assertEqual(state["animation"]["id"], "flight_123")
        self.assertEqual(state["animation"]["video_url"], f"/theaters/{theater_id}/output/animations/flight_123/video.mp4")
        self.assertEqual(state["animation"]["video_duration_seconds"], 5)
        self.assertTrue(state["animation"]["loop"])
        self.assertTrue(state["animation"]["muted"])

        # Prompt resolution must show the original scene prompt, not "Image: Video"
        self.assertEqual(state["prompt"], "A soaring griffin over snow-capped mountains.")
        self.assertNotEqual(state["prompt"], "Image: Video")
        self.assertEqual(len(state["history"]), 1)
        self.assertEqual(state["history"][0]["prompt"], "A soaring griffin over snow-capped mountains.")
        self.assertEqual(state["history"][0]["animation"]["type"], "video")

        renderer = (PROJECT_ROOT / "static" / "js" / "canvas-renderers.js").read_text(encoding="utf-8")
        self.assertIn("video_duration_seconds", renderer)
        canvas = (PROJECT_ROOT / "templates" / "canvas.html").read_text(encoding="utf-8")
        obs = (PROJECT_ROOT / "templates" / "obs.html").read_text(encoding="utf-8")

        # Verify full silence safeguards
        self.assertIn("video.muted = true", renderer)
        self.assertIn("video.defaultMuted = true", renderer)
        self.assertIn("video.volume = 0", renderer)
        self.assertIn('video.setAttribute("muted", "")', renderer)
        self.assertIn("silenceVideo", renderer)

        # Verify auto-loop safeguards
        self.assertIn("autoLoop", renderer)
        self.assertIn("restartVideoLoop", renderer)
        self.assertIn('video.addEventListener("ended", restartVideoLoop)', renderer)
        self.assertIn('video.currentTime = 0', renderer)

        # Verify DOM attachment and cleanup for decoder reliability
        self.assertIn("sizingElement.appendChild(video)", renderer)
        self.assertIn("currentVideo.parentElement.removeChild(currentVideo)", renderer)

        # Verify wiring in canvas and OBS templates
        self.assertIn("playVideoAnimation", canvas)
        self.assertIn("playVideoAnimation", obs)
        self.assertIn("video-loop-silent", canvas)
        self.assertIn("video-loop-silent", obs)
