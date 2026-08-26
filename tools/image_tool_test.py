import io
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

from PIL import Image, PngImagePlugin

from components.canvas_state_service import CanvasStateService
from components.theater_manager import TheaterManager
from providers import ImageGenerationResult
from testing.base import BaseTestCase
from tools.image_tool import ImageTools


def create_fake_image_bytes() -> bytes:
    image = Image.new("RGB", (10, 10), color="blue")
    output = io.BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


class TestImageTools(BaseTestCase):
    def setUp(self):
        super().setUp()
        ImageTools._references_cache = {}
        ImageTools._reference_dir_cached = None
        self.temp_dir = tempfile.mkdtemp()
        self.manager = TheaterManager(base_theaters_dir=self.temp_dir)
        self.config = {
            "image_generation": {
                "cooldown_duration": 0,
                "cycle_length": 0,
                "provider": "hybrid-flux-gemini",
                "provider_options": {"classifier_model": "gemini-2.5-flash-lite"},
            }
        }

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _provider_result(self):
        return ImageGenerationResult(
            image_bytes=create_fake_image_bytes(),
            mime_type="image/jpeg",
            provider="hybrid-flux-gemini",
            model="fal-ai/flux-2/klein/9b",
        )

    @patch("tools.image_tool.get_image_provider")
    def test_create_image_uses_configured_provider(self, mock_get_provider):
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        tools = ImageTools(self.config, theater_id="configured_provider", theater_manager=self.manager)
        tools.stop_cycle()

        tools.create_image("a dog carrying a bag", image_name="bag_dog", display=False)
        tools.join_generation()

        mock_get_provider.assert_called_once_with(
            "hybrid-flux-gemini", {"classifier_model": "gemini-2.5-flash-lite"}
        )
        request = provider.generate.call_args.args[0]
        self.assertEqual(request.prompt, "a dog carrying a bag")
        self.assertEqual(request.references, [])
        tools.stop_cycle()

    @patch("tools.image_tool.get_image_provider")
    def test_create_image_passes_loaded_references_to_provider(self, mock_get_provider):
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        tools = ImageTools(self.config, theater_id="references", theater_manager=self.manager)
        tools.stop_cycle()
        reference_path = os.path.join(tools.reference_dir, "hero.png")
        Image.new("RGB", (10, 10), color="red").save(reference_path)
        tools._load_references()

        tools.create_image("hero at dawn", image_name="hero_at_dawn", reference_images="hero", display=False)
        tools.join_generation()

        references = provider.generate.call_args.args[0].references
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].name, "hero.png")
        self.assertEqual(references[0].mime_type, "image/png")
        tools.stop_cycle()

    @patch("tools.image_tool.get_image_provider")
    def test_create_image_saves_output_and_registers_alias(self, mock_get_provider):
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        tools = ImageTools(self.config, theater_id="alias", theater_manager=self.manager)
        tools.stop_cycle()
        created = MagicMock()
        tools.on_image_created = created

        tools.create_image("sunset scene", image_name="sunset_01", display=False)
        tools.join_generation()

        self.assertTrue(os.path.exists(tools.image_aliases["sunset_01"]))
        created.assert_called_once_with(tools.image_aliases["sunset_01"])
        tools.stop_cycle()

    def test_create_image_requires_a_provider(self):
        with self.assertRaisesRegex(ValueError, "image_generation.provider"):
            ImageTools({"image_generation": {"cooldown_duration": 0}}, "missing", self.manager)

    @patch("tools.image_tool.get_image_provider")
    def test_create_image_rejects_an_empty_required_image_name(self, mock_get_provider):
        tools = ImageTools(self.config, theater_id="required_name", theater_manager=self.manager)
        tools.stop_cycle()

        self.assertEqual(
            tools.create_image("a castle", image_name="", display=False),
            "Error: image_name is required when creating an image.",
        )
        mock_get_provider.assert_not_called()
        tools.stop_cycle()

    def test_search_image_by_metadata_matches_standard_description_and_title(self):
        tools = ImageTools(self.config, theater_id="metadata_search", theater_manager=self.manager)
        tools.stop_cycle()
        reference_path = os.path.join(tools.reference_dir, "scene.png")
        png_info = PngImagePlugin.PngInfo()
        png_info.add_text("Title", "The Candlelit Scribe")
        png_info.add_text("Description", "A chrysolic monk writing by candlelight")
        Image.new("RGB", (10, 10), color="gold").save(reference_path, pnginfo=png_info)
        tools._load_references()

        self.assertEqual(tools.search_image_by_metadata("scribe"), [reference_path])
        self.assertEqual(tools.search_image_by_metadata("chrysolic"), [reference_path])
        self.assertEqual(tools.list_references()[0]["title"], "The Candlelit Scribe")
        tools.stop_cycle()

    @patch("tools.image_tool.get_image_provider")
    def test_show_image_cycle_and_staging(self, mock_get_provider):
        tools = ImageTools(self.config, theater_id="show_cycle", theater_manager=self.manager)
        tools.stop_cycle()
        img1 = os.path.join(tools.reference_dir, "scene1.jpg")
        img2 = os.path.join(tools.reference_dir, "scene2.jpg")
        Image.new("RGB", (10, 10), color="blue").save(img1)
        Image.new("RGB", (10, 10), color="green").save(img2)

        # 1. Cold start: displays immediately
        res1 = tools.show_image("scene1.jpg")
        self.assertIn("Successfully displayed", res1)
        self.assertEqual(tools.current_cycle_image["path"], img1)
        self.assertIsNone(tools.next_cycle_image)

        # 2. Subsequent call: queues for next cycle
        res2 = tools.show_image("scene2.jpg")
        self.assertIn("queued for the next image cycle", res2)
        self.assertEqual(tools.current_cycle_image["path"], img1)
        self.assertEqual(tools.next_cycle_image["path"], img2)

        # 3. Advance cycle: promotes staged image
        advanced = tools.advance_cycle()
        self.assertEqual(advanced["path"], img2)
        self.assertEqual(tools.current_cycle_image["path"], img2)
        self.assertIsNone(tools.next_cycle_image)

        # 4. Advance cycle with no staged image: retains current image
        advanced2 = tools.advance_cycle()
        self.assertEqual(advanced2["path"], img2)
        self.assertEqual(tools.current_cycle_image["path"], img2)
        tools.stop_cycle()

    @patch("tools.image_tool.get_image_provider")
    def test_create_image_has_priority_over_show_image_in_next_cycle(self, mock_get_provider):
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        tools = ImageTools(self.config, theater_id="priority_test", theater_manager=self.manager)
        tools.stop_cycle()

        img_ref = os.path.join(tools.reference_dir, "ref.jpg")
        Image.new("RGB", (10, 10), color="blue").save(img_ref)

        # Establish current image
        tools.show_image("ref.jpg")
        self.assertEqual(tools.current_cycle_image["path"], img_ref)

        # Stage another show_image
        img_staged = os.path.join(tools.reference_dir, "staged.jpg")
        Image.new("RGB", (10, 10), color="yellow").save(img_staged)
        tools.show_image("staged.jpg")
        self.assertEqual(tools.next_cycle_image["path"], img_staged)
        self.assertEqual(tools.next_cycle_image["priority"], tools.PRIORITY_SHOW)

        # Now create_image completes in background -> should override staged show_image
        tools.create_image("a shining diamond", image_name="shining_diamond", display=True)
        tools.join_generation()

        self.assertIsNotNone(tools.next_cycle_image)
        self.assertEqual(tools.next_cycle_image["priority"], tools.PRIORITY_CREATE)
        self.assertEqual(tools.next_cycle_image["source"], "create_image")

        # Calling show_image now cannot override the higher-priority create_image
        blocked_res = tools.show_image("ref.jpg")
        self.assertIn("already has priority", blocked_res)
        self.assertEqual(tools.next_cycle_image["source"], "create_image")

        # Roll over cycle -> generated image becomes current
        advanced = tools.advance_cycle()
        self.assertEqual(advanced["source"], "create_image")
        tools.stop_cycle()

    @patch("tools.image_tool.get_image_provider")
    def test_missing_reference_returns_error_without_calling_provider(self, mock_get_provider):
        tools = ImageTools(self.config, theater_id="missing_reference", theater_manager=self.manager)
        tools.stop_cycle()

        result = tools.create_image("a castle", image_name="castle", reference_images="not-here")

        self.assertIn("Reference image 'not-here' not found", result)
        mock_get_provider.assert_not_called()
        tools.stop_cycle()

    def _make_tools_with_style(self, style: str) -> ImageTools:
        config = {
            "image_generation": {
                **self.config["image_generation"],
                "style": style,
            },
        }
        tools = ImageTools(config, theater_id="style_test", theater_manager=self.manager)
        tools.stop_cycle()
        return tools

    def test_default_style_loaded_from_config(self):
        tools = self._make_tools_with_style("  watercolor impressionist  ")
        self.assertEqual(tools.default_style, "watercolor impressionist")
        tools.stop_cycle()

    def test_default_style_appended_when_absent(self):
        tools = self._make_tools_with_style("watercolor impressionist")
        result = tools._apply_default_style("a lone samurai on a hill")
        self.assertEqual(result, "a lone samurai on a hill\n\nStyle: watercolor impressionist")
        tools.stop_cycle()

    def test_default_style_not_appended_when_style_present(self):
        tools = self._make_tools_with_style("watercolor impressionist")
        prompt = "a lone samurai on a hill. Style: oil painting"
        result = tools._apply_default_style(prompt)
        self.assertEqual(result, prompt)
        tools.stop_cycle()

    def test_default_style_empty_no_change(self):
        tools = ImageTools(self.config, theater_id="no_style", theater_manager=self.manager)
        tools.stop_cycle()
        prompt = "a lone samurai on a hill"
        result = tools._apply_default_style(prompt)
        self.assertEqual(result, prompt)
        tools.stop_cycle()

    @patch("tools.image_tool.get_image_provider")
    def test_adventure_mode_throttles_create_image_until_story_plan_completed(self, mock_get_provider):
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        config = {
            **self.config,
        }
        tools = ImageTools(config, theater_id="adv_create_test", theater_manager=self.manager, adventure_mode=True)
        tools.stop_cycle()
        self.assertTrue(tools.adventure_mode)
        self.assertFalse(tools.is_story_plan_completed)

        # 1. Attempt without completed story plan is rejected
        res = tools.create_image("a scenic mountain", image_name="scenic_mountain", display=False)
        self.assertIn("Error: Cannot create image: Waiting for the story planner to complete its response", res)
        mock_get_provider.assert_not_called()

        # 2. Record story plan completion -> enables create_image
        tools.record_story_plan_completed()
        self.assertTrue(tools.is_story_plan_completed)

        res2 = tools.create_image("a scenic mountain", image_name="scenic_mountain", display=False)
        self.assertIn("Image generation started in background", res2)
        tools.join_generation()
        # Consumes the story plan completion
        self.assertFalse(tools.is_story_plan_completed)

        # 3. Subsequent call without completed plan is rejected
        res3 = tools.create_image("another mountain", image_name="another_mountain", display=False)
        self.assertIn("Error: Cannot create image: Waiting for the story planner to complete its response", res3)

        # 4. New story plan completion allows it again
        tools.record_story_plan_completed()
        self.assertTrue(tools.is_story_plan_completed)
        res4 = tools.create_image("another mountain", image_name="another_mountain", display=False)
        self.assertIn("Image generation started in background", res4)
        tools.join_generation()
        self.assertFalse(tools.is_story_plan_completed)
        tools.stop_cycle()

    @patch("tools.image_tool.get_image_provider")
    def test_adventure_mode_throttles_show_image_until_story_plan_completed(self, mock_get_provider):
        config = {
            **self.config,
        }
        tools = ImageTools(config, theater_id="adv_show_test", theater_manager=self.manager, adventure_mode=True)
        tools.stop_cycle()
        self.assertTrue(tools.adventure_mode)
        self.assertFalse(tools.is_story_plan_completed)

        img_path = os.path.join(tools.reference_dir, "test_card.jpg")
        Image.new("RGB", (10, 10), color="purple").save(img_path)

        # 1. Attempt without completed story plan is rejected
        res = tools.show_image("test_card.jpg")
        self.assertIn("Error: Cannot show image: Waiting for the story planner to complete its response", res)

        # 2. Record story plan completion -> enables show_image
        tools.record_story_plan_completed()
        self.assertTrue(tools.is_story_plan_completed)

        res2 = tools.show_image("test_card.jpg")
        self.assertIn("Successfully displayed", res2)
        # Consumes the story plan completion
        self.assertFalse(tools.is_story_plan_completed)

        # 3. Subsequent call without completed story plan is rejected
        res3 = tools.show_image("test_card.jpg")
        self.assertIn("Error: Cannot show image: Waiting for the story planner to complete its response", res3)

        # 4. New story plan completion allows it again
        tools.record_story_plan_completed()
        self.assertTrue(tools.is_story_plan_completed)
        res4 = tools.show_image("test_card.jpg")
        self.assertIn("queued for the next image cycle", res4)
        self.assertFalse(tools.is_story_plan_completed)
        tools.stop_cycle()

    @patch("tools.image_tool.get_image_provider")
    def test_non_adventure_mode_does_not_throttle(self, mock_get_provider):
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        tools = ImageTools(self.config, theater_id="non_adv_test", theater_manager=self.manager)
        tools.stop_cycle()
        self.assertFalse(tools.adventure_mode)
        self.assertTrue(tools.is_story_plan_completed)

        res = tools.create_image("a scenic valley", image_name="scenic_valley", display=False)
        self.assertIn("Image generation started in background", res)
        tools.join_generation()
        self.assertTrue(tools.is_story_plan_completed)
        tools.stop_cycle()

    @patch("tools.image_tool.get_image_provider")
    def test_create_image_saves_full_quality_and_compressed_webp_and_displays_webp(self, mock_get_provider):
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        mock_canvas_service = MagicMock()
        tools = ImageTools(
            self.config,
            theater_id="webp_test",
            theater_manager=self.manager,
            canvas_state_service=mock_canvas_service,
        )
        tools.stop_cycle()

        tools.create_image("a glowing forest", image_name="forest_01", display=True)
        tools.join_generation()

        # Full quality JPEG exists on disk
        full_quality_path = tools.image_aliases["forest_01"]
        self.assertTrue(os.path.exists(full_quality_path))
        self.assertTrue(full_quality_path.endswith(".jpg"))

        # Compressed WebP exists on disk
        webp_path = os.path.splitext(full_quality_path)[0] + ".webp"
        self.assertTrue(os.path.exists(webp_path))

        # Canvas state service received the WebP path for display
        mock_canvas_service.show_image.assert_called_once()
        args, kwargs = mock_canvas_service.show_image.call_args
        displayed_path = args[0]
        self.assertTrue(displayed_path.endswith(".webp"))
        self.assertEqual(displayed_path, webp_path)
        tools.stop_cycle()

    def test_show_image_sends_compressed_webp_to_canvas_state_service(self):
        mock_canvas_service = MagicMock()
        tools = ImageTools(
            self.config,
            theater_id="show_webp_test",
            theater_manager=self.manager,
            canvas_state_service=mock_canvas_service,
        )
        tools.stop_cycle()

        img_ref = os.path.join(tools.reference_dir, "ref_card.jpg")
        Image.new("RGB", (20, 20), color="purple").save(img_ref)

        tools.show_image("ref_card.jpg")

        mock_canvas_service.show_image.assert_called_once()
        args, kwargs = mock_canvas_service.show_image.call_args
        displayed_path = args[0]
        self.assertTrue(displayed_path.endswith(".webp"))
        self.assertTrue(os.path.exists(displayed_path))
        tools.stop_cycle()

    def test_show_image_resolves_underscore_alias_and_publishes_webp_to_canvas(self):
        canvas_state_service = CanvasStateService(self.manager)
        tools = ImageTools(
            self.config,
            theater_id="monk_alias_test",
            theater_manager=self.manager,
            canvas_state_service=canvas_state_service,
        )
        tools.stop_cycle()
        reference_path = os.path.join(tools.reference_dir, "the monk.png")
        Image.new("RGB", (20, 20), color="gold").save(reference_path)
        tools._load_references()

        self.assertIn("Successfully displayed", tools.show_image("the_monk"))

        state = canvas_state_service.latest_state("monk_alias_test")
        expected_webp = os.path.join(tools.output_dir, "the monk.webp")
        self.assertEqual(
            canvas_state_service.get("monk_alias_test").shown_image_path,
            expected_webp,
        )
        self.assertTrue(os.path.exists(expected_webp))
        self.assertEqual(
            state["latest"],
            "/theaters/monk_alias_test/output/artifacts/images/the monk.webp",
        )
        tools.stop_cycle()

    def test_show_image_refreshes_references_added_after_session_start(self):
        tools = ImageTools(self.config, theater_id="late_reference", theater_manager=self.manager)
        tools.stop_cycle()
        reference_path = os.path.join(tools.reference_dir, "the monk.png")
        Image.new("RGB", (20, 20), color="gold").save(reference_path)

        self.assertIn("Successfully displayed", tools.show_image("the_monk"))
        self.assertEqual(tools.currently_displayed_image_path, reference_path)
        tools.stop_cycle()

