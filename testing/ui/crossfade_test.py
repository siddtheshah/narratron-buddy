"""Tests for canvas image transitions and effects."""

from pathlib import Path
from unittest.mock import MagicMock

from api_server.shared import PROJECT_ROOT
from components.theater_manager import TheaterManager
from testing.ui.base import UITestCase


class TestCrossfade(UITestCase):
    def test_canvas_state_stores_transition_and_effect(self):
        image = self.workspace / "shown.jpg"
        image.write_bytes(b"image")
        manager = self.make_canvas_state("transition_storage")

        for transition, effect in (("crossfade", "gleam3"), ("fade", "sparkle"), ("none", "none")):
            manager.update_shown_image(str(image), transition=transition, effect=effect)
            self.assertEqual(manager.shown_image_transition, transition)
            self.assertEqual(manager.shown_image_effect, effect)

        manager.update_shown_image(str(image), transition=None, effect=None)
        self.assertEqual(manager.shown_image_transition, "crossfade")
        self.assertEqual(manager.shown_image_effect, "gleam3")

    def test_show_image_forwards_transition_and_effect_to_callback(self):
        from tools.image_tool import ImageTools

        tool = ImageTools(
            config={"image_generation": {"cooldown_duration": 0, "provider": "hybrid-flux-gemini"}},
            theater_id="image_tool_transition",
            theater_manager=TheaterManager(base_theaters_dir=self.theaters_dir),
        )

        image = Path(tool.output_dir) / "test_image.jpg"
        image.write_bytes(b"image")
        tool.image_aliases["test_image"] = str(image)
        tool.on_show_image = MagicMock()
        tool._schedule_cooldown_timer = MagicMock()

        for transition, effect in (("crossfade", "gleam3"), ("fade", "sparkle"), ("none", "none")):
            tool.last_show_time = 0
            tool.current_cycle_image = None
            tool.currently_displayed_image_path = None
            result = tool.show_image("test_image", transition=transition, effect=effect)
            self.assertIn("Successfully", result)
            tool.on_show_image.assert_called_once_with(
                str(image), transition=transition, effect=effect
            )
            tool.on_show_image.reset_mock()

    def test_latest_state_contains_transition_and_effect(self):
        image = self.workspace / "shown.jpg"
        image.write_bytes(b"image")
        manager = self.make_canvas_state("latest_state")

        for transition, effect in (("crossfade", "gleam3"), ("fade", "sparkle"), ("none", "none")):
            manager.update_shown_image(str(image), transition=transition, effect=effect)
            state = manager.get_latest_state()
            self.assertEqual(state["transition"], transition)
            self.assertEqual(state["effect"], effect)

    def test_canvas_template_supports_crossfade(self):
        template_path = PROJECT_ROOT / "templates" / "canvas.html"
        content = template_path.read_text(encoding="utf-8")

        for selector in (".t-crossfade", ".t-fade"):
            self.assertIn(selector, content)
        self.assertIn('id="ghost-image"', content)
        self.assertIn("data.transition", content)

    def test_canvas_template_contains_agent_activity_indicators(self):
        template_path = PROJECT_ROOT / "templates" / "canvas.html"
        content = template_path.read_text(encoding="utf-8")
        self.assertIn('id="agent-drawing-indicator"', content)
        self.assertIn('id="agent-dice-indicator"', content)
        self.assertIn("toolActivity.dice_rolling", content)
        self.assertIn("data.tool_activity", content)

    def test_canvas_template_renders_scene_descriptions_in_white_italics(self):
        template_path = PROJECT_ROOT / "templates" / "canvas.html"
        content = template_path.read_text(encoding="utf-8")

        self.assertIn(".narration", content)
        self.assertIn(".scene-description", content)
        self.assertIn(".scene-narration", content)
        self.assertIn("font-style: italic", content)
        self.assertIn("data.narration", content)
