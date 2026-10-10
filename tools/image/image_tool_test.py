import io
import os
import shutil
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image, PngImagePlugin

from components.canvas.canvas_state_service import CanvasStateService
from components.theater_manager import TheaterManager
from providers import ImageGenerationRequest, ImageGenerationResult, ImageProviderError, TextResponseProvider
from testing.base import BaseTestCase
from testing.reference_manager_fixture import make_reference_manager
from tools.image import ImageTools
from tools.base_tool import CANVAS_PINNED_MESSAGE
from components.reference_manager import Character, CharacterLookupResult, ReferenceManager


def create_fake_image_bytes() -> bytes:
    image = Image.new("RGB", (10, 10), color="blue")
    output = io.BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


class TestImageTools(BaseTestCase):
    def test_show_image_requires_handle_and_resolves_latest_scene(self) -> None:
        theater = self.manager.theater("scene_handles")
        scene_dir = theater.scenes_dir() / "Stormy Battlefield"
        scene_dir.mkdir(parents=True)
        first = scene_dir / "1.png"
        latest = scene_dir / "2.png"
        Image.new("RGB", (10, 10), "blue").save(first)
        reference_manager = make_reference_manager(theater)
        reference_manager.available_scene_images = MagicMock(return_value={"Stormy Battlefield": str(first)})
        tools = self.make_image_tools(
            self.config, theater_id=theater.theater_id, theater_manager=self.manager,
            reference_manager=reference_manager,
        )
        self.assertIn("requires a reference handle", tools.show_image(str(first)))
        self.assertIsNone(tools.currently_displayed_image_path)
        Image.new("RGB", (10, 10), "red").save(latest)
        reference_manager.available_scene_images.return_value = {"Stormy Battlefield": str(latest)}
        result = tools.show_image(reference_handle="Stormy Battlefield")
        self.assertIn("Successfully displayed 'Stormy Battlefield'", result)
        self.assertNotIn(str(latest), result)
        self.assertEqual(tools.currently_displayed_image_path, str(latest))

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.manager = TheaterManager(base_theaters_dir=self.temp_dir)
        self.config = {
            "visuals": {
                "cycle_length": 0,
                "model": "hybrid-flux-gemini",
                "model_options": {"classifier_model": "gemini-2.5-flash-lite"},
            },
            "image_generation": {
                "cooldown_duration": 0,
            }
        }

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def make_image_tools(self, config, theater_id, theater_manager, canvas_state_service=None, **kwargs):
        canvas_service = canvas_state_service or CanvasStateService(theater_manager)
        canvas_manager = canvas_service if isinstance(canvas_service, MagicMock) else canvas_service.get(theater_id)
        theater = theater_manager.theater(theater_id)
        if config:
            theater_manager.get_theater_config = MagicMock(return_value=config)
        return ImageTools(
            theater,
            canvas_manager=canvas_manager,
            **kwargs,
        )

    def _provider_result(self):
        return ImageGenerationResult(
            image_bytes=create_fake_image_bytes(),
            mime_type="image/jpeg",
            provider="hybrid-flux-gemini",
            model="fal-ai/flux-2/klein/9b",
        )

    @patch("tools.image.image_tool.get_image_provider")
    def test_create_image_cleans_prompt_before_provider_and_metadata(self, mock_get_provider: MagicMock) -> None:
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        tools = self.make_image_tools(self.config, theater_id="prompt_cleanup", theater_manager=self.manager)
        with self.assertLogs("tools.image.image_tool", level="DEBUG") as logs:
            tools.create_image("A duel, no graphic content, subdued violence, sparks fly. Keep the scene non-graphic, intense, and cinematic.", image_name="duel", display=False)
            tools.join_generation()
        request = provider.generate.call_args.args[0]
        self.assertEqual(request.prompt, "A duel, graphic content, explicit violence, sparks fly. Keep the scene graphic, intense, and cinematic.")
        self.assertTrue(any("Image prompt cleanup original=" in entry for entry in logs.output))
        paths = list(tools.theater.image_artifacts_dir().glob("*.jpg"))
        self.assertEqual(len(paths), 1)
        with Image.open(paths[0]) as image:
            self.assertNotIn("no graphic content", str(image.getexif()))
            self.assertIn("explicit violence", str(image.getexif()))

    @patch("tools.image.image_tool.get_image_provider")
    def test_create_image_rejects_empty_cleaned_prompt(self, mock_get_provider: MagicMock) -> None:
        tools = self.make_image_tools(self.config, theater_id="cleanup_empty", theater_manager=self.manager)
        result = tools.create_image("", image_name="empty", display=False)
        self.assertIn("Error: image_prompt", result)
        mock_get_provider.assert_not_called()

    @patch("tools.image.image_tool.get_image_provider")
    def test_prompt_tags_attach_session_portraits_without_reference_field(self, mock_get_provider: MagicMock) -> None:
        theater = self.manager.theater("tagged_characters")
        (theater.characters_dir() / "Arthur Modella").mkdir(parents=True)
        Image.new("RGB", (8, 8), "green").save(theater.characters_dir() / "Arthur Modella" / "1.png")
        reference_manager = ReferenceManager(theater, MagicMock(spec=TextResponseProvider))
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        tools = self.make_image_tools(
            self.config, theater_id=theater.theater_id, theater_manager=self.manager,
            reference_manager=reference_manager,
        )
        prompt = "<Arthur Modella> flashes a wand."
        tools.create_image(prompt, image_name="wand_flash", display=False)
        tools.join_generation()
        request = provider.generate.call_args.args[0]
        self.assertIn(prompt, request.prompt)
        self.assertEqual(len(request.references), 1)
        self.assertEqual(request.references[0].label, "Arthur Modella")
        self.assertEqual(request.references[0].data, (theater.characters_dir() / "Arthur Modella" / "1.png").read_bytes())
        image_files = list(theater.image_artifacts_dir().glob("*.jpg"))
        self.assertEqual(len(image_files), 1)
        with Image.open(image_files[0]) as image:
            metadata = str(image.getexif())
        self.assertIn("References: <Arthur Modella>", metadata)
        self.assertNotIn("References: 1.png", metadata)
        path = reference_manager.get_character_visual_path("Arthur Modella")
        self.assertIsNone(tools.visual.resolve_image_path(path))
        self.assertIn("Error", tools.show_image(path))

    @patch("tools.image.image_tool.get_image_provider")
    def test_pinned_canvas_blocks_image_generation_and_display(self, mock_get_provider):
        tools = self.make_image_tools(self.config, theater_id="pinned", theater_manager=self.manager)
        tools.visual.set_pinned(True)

        self.assertEqual(tools.create_image("a new scene", image_name="new_scene"), CANVAS_PINNED_MESSAGE)
        self.assertEqual(tools.show_image("missing-is-never-resolved"), CANVAS_PINNED_MESSAGE)
        mock_get_provider.return_value.generate.assert_not_called()
        self.assertFalse(tools.is_in_flight("create_image"))

    @patch("tools.image.image_tool.get_image_provider")
    def test_orator_image_replaces_pinned_canvas_once(self, mock_get_provider: MagicMock) -> None:
        mock_get_provider.return_value.generate.return_value = self._provider_result()
        tools = self.make_image_tools(self.config, theater_id="pinned_pass", theater_manager=self.manager)
        tools.visual.set_pinned(True)
        tools.visual.request_immediate_image()
        tools.request_orator_bypass({"create_image"})
        tools.create_image("requested scene", image_name="requested")
        tools.join_generation()
        assert tools.visual.pinned is True
        assert tools.visual.shown_image_path
        assert tools.create_image("another scene", image_name="another") == CANVAS_PINNED_MESSAGE
        assert tools.show_image("requested") == CANVAS_PINNED_MESSAGE
        mock_get_provider.return_value.generate.assert_called_once()
        assert not tools._pending_cycle_calls

    @patch("tools.image.image_tool.get_image_provider")
    def test_create_image_uses_configured_provider(self, mock_get_provider):
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        tools = self.make_image_tools(self.config, theater_id="configured_provider", theater_manager=self.manager)

        tools.create_image("a dog carrying a bag", image_name="bag_dog", display=False)
        tools.join_generation()

        mock_get_provider.assert_called_once_with(
            "hybrid-flux-gemini", {"classifier_model": "gemini-2.5-flash-lite"}
        )
        request = provider.generate.call_args.args[0]
        self.assertEqual(request.prompt, "a dog carrying a bag")
        self.assertEqual(request.references, [])

    @patch("tools.image.image_tool.get_image_provider")
    def test_create_image_passes_loaded_references_to_provider(self, mock_get_provider):
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        tools = self.make_image_tools(self.config, theater_id="references", theater_manager=self.manager)
        reference_path = os.path.join(tools.reference_dir, "hero.png")
        Image.new("RGB", (10, 10), color="red").save(reference_path)
        tools._load_references()

        tools.create_image("<hero> at dawn", image_name="hero_at_dawn", display=False)
        tools.join_generation()

        references = provider.generate.call_args.args[0].references
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].name, "hero.png")
        self.assertEqual(references[0].mime_type, "image/png")

    @patch("tools.image.image_tool.get_image_provider")
    def test_create_image_saves_output_and_registers_alias(self, mock_get_provider):
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        tools = self.make_image_tools(self.config, theater_id="alias", theater_manager=self.manager)
        created = MagicMock()
        tools.on_image_created = created

        tools.create_image("sunset scene", image_name="sunset_01", display=False)
        tools.join_generation()

        alias_path = tools.visual.resolve_image_path("sunset_01")
        self.assertIsNotNone(alias_path)
        self.assertTrue(os.path.exists(alias_path))
        created.assert_called_once_with(alias_path)

    def test_create_image_requires_a_provider(self):
        with self.assertRaisesRegex(ValueError, "visuals.model"):
            self.make_image_tools({"image_generation": {"cooldown_duration": 0}}, "missing", self.manager)

    @patch("tools.image.image_tool.get_image_provider")
    def test_create_image_rejects_an_empty_required_image_name(self, mock_get_provider):
        tools = self.make_image_tools(self.config, theater_id="required_name", theater_manager=self.manager)

        self.assertEqual(
            tools.create_image("a castle", image_name="", display=False),
            "Error: image_name is required when creating an image.",
        )
        mock_get_provider.assert_not_called()

    def test_search_image_by_metadata_matches_standard_description_and_title(self):
        tools = self.make_image_tools(self.config, theater_id="metadata_search", theater_manager=self.manager)
        reference_path = os.path.join(tools.reference_dir, "scene.png")
        png_info = PngImagePlugin.PngInfo()
        png_info.add_text("Title", "The Candlelit Scribe")
        png_info.add_text("Description", "A chrysolic monk writing by candlelight")
        Image.new("RGB", (10, 10), color="gold").save(reference_path, pnginfo=png_info)
        tools._load_references()

        self.assertEqual(tools.search_image_by_metadata("scribe"), [reference_path])
        self.assertEqual(tools.search_image_by_metadata("chrysolic"), [reference_path])
        self.assertEqual(tools.list_references()[0]["title"], "The Candlelit Scribe")

    @patch("tools.image.image_tool.get_image_provider")
    def test_show_image_cycle_and_staging(self, mock_get_provider):
        tools = self.make_image_tools(self.config, theater_id="show_cycle", theater_manager=self.manager)
        visual = tools.visual
        visual.stop_cycle()
        img1 = os.path.join(tools.reference_dir, "scene1.jpg")
        img2 = os.path.join(tools.reference_dir, "scene2.jpg")
        Image.new("RGB", (10, 10), color="blue").save(img1)
        Image.new("RGB", (10, 10), color="green").save(img2)

        # 1. Cold start: displays immediately
        res1 = tools.show_image("scene1.jpg")
        self.assertIn("Successfully displayed", res1)
        self.assertEqual(visual.current_cycle_visual["path"], img1)
        self.assertIsNone(visual.next_cycle_image)

        # 2. Subsequent call: queues for next cycle
        res2 = tools.show_image("scene2.jpg")
        self.assertIn("queued for its turn on the canvas", res2)
        self.assertEqual(visual.current_cycle_visual["path"], img1)
        self.assertEqual(visual.next_cycle_image["path"], img2)

        # 3. Advance cycle: promotes staged image
        advanced = visual.advance_cycle()
        self.assertEqual(advanced["path"], img2)
        self.assertEqual(visual.current_cycle_visual["path"], img2)
        self.assertIsNone(visual.next_cycle_image)

        # 4. Advance cycle with no staged image: retains current image
        advanced2 = visual.advance_cycle()
        self.assertEqual(advanced2["path"], img2)
        self.assertEqual(visual.current_cycle_visual["path"], img2)
        visual.stop_cycle()

    def test_starting_image_is_displayed_when_the_canvas_initializes(self):
        theater = self.manager.theater("starting_image")
        reference_dir = theater.references_dir()
        reference_dir.mkdir(parents=True, exist_ok=True)
        image_path = reference_dir / "opening scene.jpg"
        Image.new("RGB", (20, 20), color="orange").save(image_path)
        (theater.directory() / "theater.yaml").write_text(
            "starting_image: opening_scene\n", encoding="utf-8"
        )
        canvas_state_service = CanvasStateService(self.manager)
        canvas_state = canvas_state_service.get("starting_image")

        self.assertEqual(
            canvas_state.visual.shown_image_path,
            str(image_path),
        )
        self.assertEqual(canvas_state.get_latest_state()["latest"], "/theaters/starting_image/references/opening%20scene.jpg")

    @patch("tools.image.image_tool.get_image_provider")
    def test_create_image_and_show_image_keep_separate_canvas_queue_slots(self, mock_get_provider: MagicMock) -> None:
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        tools = self.make_image_tools(self.config, theater_id="priority_test", theater_manager=self.manager)
        visual = tools.visual
        visual.stop_cycle()

        img_ref = os.path.join(tools.reference_dir, "ref.jpg")
        Image.new("RGB", (10, 10), color="blue").save(img_ref)

        # Establish current image
        tools.show_image("ref.jpg")
        self.assertEqual(visual.current_cycle_visual["path"], img_ref)

        # Stage another show_image
        img_staged = os.path.join(tools.reference_dir, "staged.jpg")
        Image.new("RGB", (10, 10), color="yellow").save(img_staged)
        tools.show_image("staged.jpg")
        self.assertEqual(visual.next_cycle_image["path"], img_staged)
        self.assertEqual(visual.next_cycle_image["priority"], visual.PRIORITY_SHOW)

        # Generation joins behind the saved image already waiting.
        tools.create_image("a shining diamond", image_name="shining_diamond", display=True)
        tools.join_generation()

        self.assertIsNotNone(visual.next_cycle_image)
        self.assertEqual([item["source"] for item in visual.pending_visuals], ["show_image", "create_image"])

        # A newer saved image replaces only its own source's slot.
        queued_res = tools.show_image("ref.jpg")
        self.assertIn("queued", queued_res)
        self.assertEqual(visual.next_cycle_image["path"], img_ref)
        self.assertEqual(len(visual.pending_visuals), 2)

        # Both sources get a turn in order.
        advanced = visual.advance_cycle()
        self.assertEqual(advanced["source"], "show_image")
        advanced = visual.advance_cycle()
        self.assertEqual(advanced["source"], "create_image")
        visual.stop_cycle()

    @patch("tools.image.image_tool.get_image_provider")
    def test_missing_reference_returns_error_without_calling_provider(self, mock_get_provider):
        tools = self.make_image_tools(self.config, theater_id="missing_reference", theater_manager=self.manager)

        result = tools.create_image("a castle with <not-here>", image_name="castle")

        self.assertIn("Error: Character visual '<not-here>' not found", result)
        mock_get_provider.assert_not_called()

    def _make_tools_with_style(self, style: str) -> ImageTools:
        config = {
            "visuals": {
                **self.config["visuals"],
                "style": style,
            },
            "image_generation": {
                **self.config["image_generation"],
            },
        }
        tools = self.make_image_tools(config, theater_id="style_test", theater_manager=self.manager)
        return tools

    def test_default_style_loaded_from_config(self):
        tools = self._make_tools_with_style("  watercolor impressionist  ")
        self.assertEqual(tools.default_style, "watercolor impressionist")

    def test_default_style_appended_when_absent(self):
        tools = self._make_tools_with_style("watercolor impressionist")
        result = tools._apply_default_style("a lone samurai on a hill")
        self.assertEqual(result, "a lone samurai on a hill\n\nStyle: watercolor impressionist")

    def test_default_style_not_appended_when_style_present(self):
        tools = self._make_tools_with_style("watercolor impressionist")
        prompt = "a lone samurai on a hill. Style: oil painting"
        result = tools._apply_default_style(prompt)
        self.assertEqual(result, prompt)

    def test_default_style_empty_no_change(self):
        tools = self.make_image_tools(self.config, theater_id="no_style", theater_manager=self.manager)
        prompt = "a lone samurai on a hill"
        result = tools._apply_default_style(prompt)
        self.assertEqual(result, prompt)

    @patch("tools.image.image_tool.get_image_provider")
    def test_adventure_mode_throttles_create_image_until_story_plan_completed(self, mock_get_provider):
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        config = {
            **self.config,
        }
        tools = self.make_image_tools(config, theater_id="adv_create_test", theater_manager=self.manager, adventure_mode=True)
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

    @patch("tools.image.image_tool.get_image_provider")
    def test_adventure_mode_throttles_show_image_until_story_plan_completed(self, mock_get_provider):
        config = {
            **self.config,
        }
        tools = self.make_image_tools(config, theater_id="adv_show_test", theater_manager=self.manager, adventure_mode=True)
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
        self.assertIn("queued for its turn on the canvas", res4)
        self.assertFalse(tools.is_story_plan_completed)

    @patch("tools.image.image_tool.get_image_provider")
    def test_non_adventure_mode_does_not_throttle(self, mock_get_provider):
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        tools = self.make_image_tools(self.config, theater_id="non_adv_test", theater_manager=self.manager)
        self.assertFalse(tools.adventure_mode)
        self.assertTrue(tools.is_story_plan_completed)

        res = tools.create_image("a scenic valley", image_name="scenic_valley", display=False)
        self.assertIn("Image generation started in background", res)
        tools.join_generation()
        self.assertTrue(tools.is_story_plan_completed)

    @patch("tools.image.image_tool.get_image_provider")
    def test_create_image_saves_full_quality_and_compressed_webp_and_displays_webp(self, mock_get_provider):
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        mock_canvas_service = MagicMock()
        mock_canvas_service.visual.pinned = False
        tools = self.make_image_tools(
            self.config,
            theater_id="webp_test",
            theater_manager=self.manager,
            canvas_state_service=mock_canvas_service,
        )

        tools.create_image("a glowing forest", image_name="forest_01", display=True)
        tools.join_generation()

        # Full quality JPEG exists on disk
        full_quality_path = str(next(Path(tools.output_dir).glob("forest_01_*.jpg")))
        self.assertTrue(os.path.exists(full_quality_path))
        self.assertTrue(full_quality_path.endswith(".jpg"))

        # Compressed WebP exists on disk
        webp_path = os.path.splitext(full_quality_path)[0] + ".webp"
        self.assertTrue(os.path.exists(webp_path))

        # Canvas state service received the WebP path for display
        mock_canvas_service.visual.update_visual.assert_called_once()
        kwargs = mock_canvas_service.visual.update_visual.call_args.kwargs
        displayed_path = kwargs.get("display_path")
        self.assertTrue(displayed_path.endswith(".webp"))
        self.assertEqual(displayed_path, webp_path)

    @patch("tools.image.image_tool.get_image_provider")
    def test_create_image_preserves_theater_workspace_assets(self, mock_get_provider):
        """A generated image must never replace or remove its theater workspace."""
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        theater_id = "preserve_workspace"
        self.manager.create_theater(name="Preserve workspace", theater_id=theater_id)
        theater_dir = self.manager.theater(theater_id).directory()
        config_path = theater_dir / "theater.yaml"
        reference_path = theater_dir / "references" / "narratron_avatar.jpg"

        tools = self.make_image_tools(self.config, theater_id=theater_id, theater_manager=self.manager)
        tools.create_image("a moonlit observatory", image_name="observatory", display=False)
        tools.join_generation()

        self.assertTrue(theater_dir.is_dir())
        self.assertTrue(config_path.is_file())
        self.assertTrue(reference_path.is_file())
        self.assertTrue(any(Path(tools.output_dir).glob("observatory_*.jpg")))

    def test_show_image_sends_compressed_webp_to_canvas_state_service(self):
        mock_canvas_service = MagicMock()
        mock_canvas_service.visual.pinned = False
        tools = self.make_image_tools(
            self.config,
            theater_id="show_webp_test",
            theater_manager=self.manager,
            canvas_state_service=mock_canvas_service,
        )

        img_ref = os.path.join(tools.reference_dir, "ref_card.jpg")
        Image.new("RGB", (20, 20), color="purple").save(img_ref)

        tools.show_image("ref_card.jpg")

        mock_canvas_service.visual.update_visual.assert_called_once()
        kwargs = mock_canvas_service.visual.update_visual.call_args.kwargs
        displayed_path = kwargs.get("display_path")
        self.assertTrue(displayed_path.endswith(".webp"))
        self.assertTrue(os.path.exists(displayed_path))

    def test_show_image_resolves_underscore_alias_and_publishes_webp_to_canvas(self):
        canvas_state_service = CanvasStateService(self.manager)
        tools = self.make_image_tools(
            self.config,
            theater_id="monk_alias_test",
            theater_manager=self.manager,
            canvas_state_service=canvas_state_service,
        )
        reference_path = os.path.join(tools.reference_dir, "the monk.png")
        Image.new("RGB", (20, 20), color="gold").save(reference_path)
        tools._load_references()

        self.assertIn("Successfully displayed", tools.show_image("the_monk"))

        state = canvas_state_service.get("monk_alias_test").get_latest_state()
        shown_path = canvas_state_service.get("monk_alias_test").visual.shown_image_path
        self.assertTrue(shown_path.endswith(".webp"))
        self.assertIn("the monk", shown_path)
        self.assertTrue(os.path.exists(shown_path))
        self.assertTrue(state["latest"].endswith(".webp"))
        self.assertIn("the monk", state["latest"])

    def test_show_image_refreshes_references_added_after_session_start(self):
        tools = self.make_image_tools(self.config, theater_id="late_reference", theater_manager=self.manager)
        reference_path = os.path.join(tools.reference_dir, "the monk.png")
        Image.new("RGB", (20, 20), color="gold").save(reference_path)

        self.assertIn("Successfully displayed", tools.show_image("the_monk"))
        self.assertEqual(tools.currently_displayed_image_path, reference_path)

    def test_show_image_takes_priority_when_animation_is_active(self):
        canvas_state_service = CanvasStateService(self.manager)
        theater_id = "anim_priority_show"
        tools = self.make_image_tools(
            self.config,
            theater_id=theater_id,
            theater_manager=self.manager,
            canvas_state_service=canvas_state_service,
        )
        img1 = os.path.join(tools.reference_dir, "scene1.jpg")
        img2 = os.path.join(tools.reference_dir, "scene2.jpg")
        Image.new("RGB", (10, 10), color="blue").save(img1)
        Image.new("RGB", (10, 10), color="green").save(img2)

        # 1. Establish initial displayed image
        tools.show_image("scene1.jpg")
        self.assertEqual(tools.visual.current_cycle_visual["path"], img1)

        # 2. Simulate active video animation playing on canvas (occupies current cycle)
        c_state = canvas_state_service.get(theater_id)
        c_state.visual.show_video_animation({
            "id": "cascade_anim",
            "video_url": "https://example.com/video.mp4",
            "scene_prompt": "falling sand",
        })
        self.assertIsNotNone(c_state.visual.shown_video_animation)
        self.assertEqual(tools.visual.current_cycle_visual["type"], "video")

        # 3. Request new image -> cannot evict active animation; queues for next cycle
        res = tools.show_image("scene2.jpg")
        self.assertIn("queued for its turn on the canvas", res)
        self.assertEqual(tools.visual.current_cycle_visual["type"], "video")
        self.assertEqual(tools.visual.next_cycle_image["path"], img2)
        self.assertIsNotNone(c_state.visual.shown_video_animation)

        # 4. Advance cycle promotes staged image and clears active animation
        promoted = tools.visual.advance_cycle()
        self.assertEqual(promoted["path"], img2)
        self.assertEqual(tools.visual.current_cycle_visual["path"], img2)
        self.assertIsNone(tools.visual.next_cycle_image)
        self.assertIsNone(c_state.visual.shown_video_animation)

    @patch("tools.image.image_tool.get_image_provider")
    def test_images_queue_without_evicting_active_animation(self, mock_get_provider: MagicMock) -> None:
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        canvas_state_service = CanvasStateService(self.manager)
        theater_id = "anim_priority_create"
        tools = self.make_image_tools(
            self.config,
            theater_id=theater_id,
            theater_manager=self.manager,
            canvas_state_service=canvas_state_service,
        )
        img1 = os.path.join(tools.reference_dir, "scene1.jpg")
        Image.new("RGB", (10, 10), color="blue").save(img1)

        # Establish initial image
        tools.show_image("scene1.jpg")
        self.assertEqual(tools.visual.current_cycle_visual["path"], img1)

        # Simulate active animation (occupies current cycle)
        c_state = canvas_state_service.get(theater_id)
        c_state.visual.show_video_animation({
            "id": "cascade_anim_2",
            "video_url": "https://example.com/video2.mp4",
            "scene_prompt": "swirling vortex",
        })
        self.assertIsNotNone(c_state.visual.shown_video_animation)
        self.assertEqual(tools.visual.current_cycle_visual["type"], "video")

        # Create new image -> queues for next cycle with PRIORITY_CREATE without evicting active animation
        tools.create_image("ancient ruins", image_name="ruins", display=True)
        tools.join_generation()

        self.assertEqual(tools.visual.current_cycle_visual["type"], "video")
        self.assertIsNotNone(tools.visual.next_cycle_image)
        self.assertIn("ruins", tools.visual.next_cycle_image["path"])
        self.assertEqual(tools.visual.next_cycle_image["priority"], tools.visual.PRIORITY_CREATE)
        self.assertIsNotNone(c_state.visual.shown_video_animation)

        # A saved image joins behind generation while the animation remains visible.
        queued = tools.show_image("scene1.jpg")
        self.assertIn("queued", queued)

        # Advance cycle promotes created image and clears active animation
        promoted = tools.visual.advance_cycle()
        self.assertIn("ruins", promoted["path"])
        self.assertEqual(tools.visual.current_cycle_visual["path"], promoted["path"])
        self.assertEqual(tools.visual.next_cycle_image["path"], img1)
        self.assertIsNone(c_state.visual.shown_video_animation)
        tools.visual.advance_cycle()
        self.assertIsNone(tools.visual.next_cycle_image)

    def test_show_image_cycle_cooldown_schedules_and_updates(self) -> None:
        tools = self.make_image_tools(self.config, theater_id="cooldown_show", theater_manager=self.manager)
        tools.cooldown_duration = 10.0
        img1 = os.path.join(tools.reference_dir, "pic1.jpg")
        img2 = os.path.join(tools.reference_dir, "pic2.jpg")
        img3 = os.path.join(tools.reference_dir, "pic3.jpg")
        Image.new("RGB", (10, 10), color="red").save(img1)
        Image.new("RGB", (10, 10), color="green").save(img2)
        Image.new("RGB", (10, 10), color="blue").save(img3)

        res1 = tools.show_image("pic1.jpg")
        self.assertIn("Successfully displayed", res1)

        res2 = tools.show_image("pic2.jpg")
        self.assertEqual(res2, "Tool 'show_image' scheduled for next cycle when cooldown expires.")

        res3 = tools.show_image("pic3.jpg")
        self.assertEqual(res3, "Tool 'show_image' parameters updated for next cycle.")

        pending = tools.get_pending_cycle_call("image_cycle")
        self.assertIsNotNone(pending)
        self.assertEqual(pending["args"], ("pic3.jpg",))

    @patch("tools.image.image_tool.get_image_provider")
    def test_create_image_cycle_cooldown_schedules_and_updates(self, mock_get_provider: MagicMock) -> None:
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        tools = self.make_image_tools(self.config, theater_id="cooldown_create", theater_manager=self.manager)
        tools.cooldown_duration = 10.0

        res1 = tools.create_image("scene one", image_name="img1", display=False)
        self.assertIn("Image generation started", res1)

        res2 = tools.create_image("scene two", image_name="img2", display=False)
        self.assertEqual(res2, "Tool 'create_image' scheduled for next cycle when cooldown expires.")

        res3 = tools.create_image("scene three", image_name="img3", display=False)
        self.assertEqual(res3, "Tool 'create_image' parameters updated for next cycle.")

        pending = tools.get_pending_cycle_call("image_cycle")
        self.assertIsNotNone(pending)
        self.assertEqual(pending["args"], ("scene three",))
        self.assertEqual(pending["kwargs"], {"image_name": "img3", "display": False})
        tools.join_generation()

    @patch("tools.image.image_tool.get_image_provider")
    def test_image_tools_share_one_pending_slot_across_methods(self, mock_get_provider: MagicMock) -> None:
        mock_get_provider.return_value.generate.return_value = self._provider_result()
        tools = self.make_image_tools(self.config, theater_id="shared_slot", theater_manager=self.manager)
        tools.cooldown_duration = 9.0
        path = Path(tools.reference_dir) / "forest.jpg"
        Image.new("RGB", (10, 10), "green").save(path)
        with patch.object(ImageTools, "_schedule_cooldown_timer"):
            tools.show_image(path.stem)
            assert "scheduled" in tools.create_image("castle", image_name="castle")
            assert "parameters updated" in tools.show_image(path.stem, effect="dream")
            pending = tools.get_pending_cycle_call("image_cycle")
            assert pending is not None
            assert pending["func"].__name__ == "show_image"
            assert pending["kwargs"] == {"effect": "dream"}
            assert len(tools._pending_cycle_calls) == 1
            tools._last_call_times.clear()
            tools.acquire_in_flight("image_cycle")
            assert tools._execute_pending_cycle_call("image_cycle")
            assert tools.visual.next_cycle_image["effect"] == "dream"
            mock_get_provider.return_value.generate.assert_not_called()

    @patch("tools.image.image_tool.get_image_provider")
    def test_generation_holds_shared_flight_beyond_cooldown(self, mock_get_provider: MagicMock) -> None:
        tools = self.make_image_tools(self.config, theater_id="slow_generation", theater_manager=self.manager)
        entered = threading.Event()
        release = threading.Event()

        def generate(_request: ImageGenerationRequest) -> ImageGenerationResult:
            entered.set()
            assert release.wait(5)
            return self._provider_result()

        mock_get_provider.return_value.generate.side_effect = generate
        tools.on_cooldown_expired = MagicMock()
        tools.cooldown_duration = 9.0
        with patch.object(ImageTools, "_schedule_cooldown_timer"), patch.object(tools, "_dispatch_pending_cycle_call") as dispatch:
            try:
                assert "started" in tools.create_image("forest", image_name="forest")
                assert entered.wait(5)
                tools._last_call_times.clear()
                assert tools.is_in_flight("image_cycle")
                # Identical text in a different method must not be swallowed as a duplicate.
                assert "scheduled" in tools.show_image("forest")
                assert tools.get_pending_cycle_call("image_cycle") is not None
                dispatch.assert_not_called()
                tools.on_cooldown_expired.assert_not_called()
            finally:
                release.set()
                tools.join_generation()
            dispatch.assert_called_once_with("image_cycle")
            assert tools._execute_pending_cycle_call("image_cycle")
            assert not tools.is_in_flight("image_cycle")

    @patch("tools.image.image_tool.get_image_provider")
    def test_deferred_generation_holds_flight_and_failure_allows_retry(self, mock_get_provider: MagicMock) -> None:
        tools = self.make_image_tools(self.config, theater_id="deferred_failure", theater_manager=self.manager)
        path = Path(tools.reference_dir) / "forest.jpg"
        Image.new("RGB", (10, 10), "green").save(path)
        entered = threading.Event()
        release = threading.Event()

        def generate(_request: ImageGenerationRequest) -> ImageGenerationResult:
            entered.set()
            assert release.wait(5)
            raise ImageProviderError("provider unavailable")

        mock_get_provider.return_value.generate.side_effect = generate
        tools.cooldown_duration = 9.0
        with patch.object(ImageTools, "_schedule_cooldown_timer"):
            tools.show_image(path.stem)
            tools.create_image("castle", image_name="castle")
            tools._last_call_times.clear()
            tools.acquire_in_flight("image_cycle")
            try:
                assert tools._execute_pending_cycle_call("image_cycle")
                assert entered.wait(5)
                assert tools.is_in_flight("image_cycle")
            finally:
                release.set()
                tools.join_generation()
            assert not tools.is_in_flight("image_cycle")
            assert tools.get_cooldown_remaining("image_cycle") == 0.0
            mock_get_provider.return_value.generate.side_effect = None
            mock_get_provider.return_value.generate.return_value = self._provider_result()
            assert "started" in tools.create_image("castle", image_name="castle")
            tools.join_generation()

    def test_invalid_image_request_releases_shared_cooldown(self) -> None:
        tools = self.make_image_tools(self.config, theater_id="invalid_shared", theater_manager=self.manager)
        tools.cooldown_duration = 9.0
        with patch.object(ImageTools, "_schedule_cooldown_timer"):
            assert "Error:" in tools.create_image("forest", image_name="")
            assert not tools.is_in_flight("image_cycle")
            assert tools.get_cooldown_remaining("image_cycle") == 0.0
            assert "Error:" in tools.show_image("missing")
            assert not tools._pending_cycle_calls

    @patch("tools.image.image_tool.get_image_provider")
    def test_orator_pass_bypasses_shared_image_cooldown(self, mock_get_provider: MagicMock) -> None:
        tools = self.make_image_tools(self.config, theater_id="shared_pass", theater_manager=self.manager)
        mock_get_provider.return_value.generate.return_value = self._provider_result()
        tools.cooldown_duration = 9.0
        path = Path(tools.reference_dir) / "forest.jpg"
        Image.new("RGB", (10, 10), "green").save(path)
        with patch.object(ImageTools, "_schedule_cooldown_timer"):
            tools.show_image(path.stem)
            tools.show_image(path.stem, effect="dream")
            tools.request_orator_bypass({"create_image"})
            tools.visual.request_immediate_image()
            assert "started" in tools.create_image("castle", image_name="castle")
            tools.join_generation()
            assert not tools._pending_cycle_calls
            assert tools.visual.current_cycle_visual["source"] == "create_image"
            assert "scheduled" in tools.show_image(path.stem, effect="haze")
            tools.cancel_pending_cycle_call("image_cycle")

    @patch("tools.image.image_tool.get_image_provider")
    def test_shared_timer_executes_latest_call_without_agent_retry(self, mock_get_provider: MagicMock) -> None:
        tools = self.make_image_tools(self.config, theater_id="shared_timer", theater_manager=self.manager)
        mock_get_provider.return_value.generate.return_value = self._provider_result()
        tools.cooldown_duration = 0.5
        path = Path(tools.reference_dir) / "forest.jpg"
        Image.new("RGB", (10, 10), "green").save(path)
        finished = threading.Event()

        def after_call(tool_name: str, _canvas_info: dict[str, str]) -> None:
            if tool_name == "show_image" and tools.visual.next_cycle_image is not None:
                finished.set()

        tools.on_after_tool_call = after_call
        try:
            tools.show_image(path.stem)
            assert "scheduled" in tools.create_image("castle", image_name="castle")
            assert "parameters updated" in tools.show_image(path.stem, effect="dream")
            assert finished.wait(5)
            assert tools.visual.next_cycle_image["effect"] == "dream"
            mock_get_provider.return_value.generate.assert_not_called()
        finally:
            tools.cancel_pending_cycle_call("image_cycle")
            for timer in list(tools._cooldown_timers.values()):
                timer.cancel()

    @patch("tools.image.image_tool.get_image_provider")
    def test_create_image_auto_adds_reference_manager_references(self, mock_get_provider) -> None:
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        reference_manager = make_reference_manager(self.manager.theater("reference_resolution"))
        reference_manager.lookup_character.return_value = CharacterLookupResult(
            characters=[Character(name="hero", gender="nonbinary", image_reference="hero")]
        )

        tools = self.make_image_tools(
            self.config,
            theater_id="cm_auto_ref",
            theater_manager=self.manager,
            reference_manager=reference_manager,
        )
        ref_path = os.path.join(tools.reference_dir, "hero.png")
        Image.new("RGB", (10, 10), color="green").save(ref_path)
        tools._load_references()

        # Prompt mentions 'hero'; lookup_character returns the hero reference
        tools.create_image("hero walks on a forest path", image_name="forest_path", display=False)
        tools.join_generation()

        reference_manager.lookup_character.assert_called_with("hero walks on a forest path", name_only=True)
        references = provider.generate.call_args.args[0].references
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].name, "hero.png")

    @patch("tools.image.image_tool.get_image_provider")
    def test_create_image_deduplicates_reference_manager_references(self, mock_get_provider) -> None:
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        reference_manager = make_reference_manager(self.manager.theater("reference_resolution"))
        reference_manager.lookup_character.return_value = CharacterLookupResult(
            characters=[Character(name="hero", gender="nonbinary", image_reference="hero")]
        )

        tools = self.make_image_tools(
            self.config,
            theater_id="cm_dedup_ref",
            theater_manager=self.manager,
            reference_manager=reference_manager,
        )
        ref_path = os.path.join(tools.reference_dir, "hero.png")
        Image.new("RGB", (10, 10), color="green").save(ref_path)
        tools._load_references()

        # Repeated or matching tags deduplicate
        tools.create_image("<hero> and <hero> on a forest path", image_name="forest_path1", display=False)
        tools.join_generation()

        references = provider.generate.call_args.args[0].references
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].name, "hero.png")

    @patch("tools.image.image_tool.get_image_provider")
    def test_create_image_no_character_match_in_prompt_does_not_pull_references(self, mock_get_provider) -> None:
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        reference_manager = make_reference_manager(self.manager.theater("reference_resolution"))
        reference_manager.lookup_character.return_value = CharacterLookupResult(characters=[], player=None)

        tools = self.make_image_tools(
            self.config,
            theater_id="cm_no_match",
            theater_manager=self.manager,
            reference_manager=reference_manager,
        )

        tools.create_image("a scenic waterfall in the mountains", image_name="waterfall", display=False)
        tools.join_generation()

        reference_manager.lookup_character.assert_called_with("a scenic waterfall in the mountains", name_only=True)
        references = provider.generate.call_args.args[0].references
        self.assertEqual(len(references), 0)

    @patch("tools.image.image_tool.get_image_provider")
    def test_create_image_with_canvas_capture_handle_in_prompt(self, mock_get_provider) -> None:
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        theater = self.manager.theater("cm_capture_ref")
        reference_manager = ReferenceManager(theater)
        captures_dir = theater.canvas_captures_dir()
        captures_dir.mkdir(parents=True, exist_ok=True)
        capture_file = captures_dir / "canvas_deadbeef.png"
        Image.new("RGB", (10, 10), color="blue").save(capture_file)
        handle = reference_manager.add_canvas_capture(capture_file)

        tools = self.make_image_tools(
            self.config,
            theater_id="cm_capture_ref",
            theater_manager=self.manager,
            reference_manager=reference_manager,
        )

        tools.create_image(f"A new drawing based on <{handle}>", image_name="canvas_drawing", display=False)
        tools.join_generation()

        references = provider.generate.call_args.args[0].references
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].name, "canvas_deadbeef.png")
        self.assertEqual(references[0].label, handle)

    @patch("tools.image.image_tool.get_image_provider")
    def test_create_image_attaches_both_character_and_canvas_capture(self, mock_get_provider) -> None:
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        theater = self.manager.theater("cm_char_and_capture")
        reference_manager = ReferenceManager(theater)
        hero_dir = theater.characters_dir() / "hero"
        hero_dir.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (10, 10), color="green").save(hero_dir / "1.png")
        reference_manager.create_or_update_character("hero", image_reference=str(hero_dir / "1.png"))

        captures_dir = theater.canvas_captures_dir()
        captures_dir.mkdir(parents=True, exist_ok=True)
        capture_file = captures_dir / "canvas_12345678.png"
        Image.new("RGB", (10, 10), color="blue").save(capture_file)
        handle = reference_manager.add_canvas_capture(capture_file)

        tools = self.make_image_tools(
            self.config,
            theater_id="cm_char_and_capture",
            theater_manager=self.manager,
            reference_manager=reference_manager,
        )

        tools.create_image(f"<hero> stands near <{handle}>", image_name="hero_capture", display=False)
        tools.join_generation()

        references = provider.generate.call_args.args[0].references
        self.assertEqual(len(references), 2)
        ref_names = [r.name for r in references]
        self.assertIn("1.png", ref_names)
        self.assertIn("canvas_12345678.png", ref_names)

    def test_show_image_silently_rejects_narratron_avatar(self) -> None:
        """Calling show_image with narratron_avatar returns success but does not update the canvas."""
        mock_canvas_service = MagicMock()
        mock_canvas_service.visual.pinned = False
        tools = self.make_image_tools(
            self.config,
            theater_id="avatar_reject_test",
            theater_manager=self.manager,
            canvas_state_service=mock_canvas_service,
        )
        avatar_ref = os.path.join(tools.reference_dir, "narratron_avatar.jpg")
        Image.new("RGB", (20, 20), color="blue").save(avatar_ref)
        tools._load_references()

        res = tools.show_image("narratron_avatar.jpg")

        self.assertIn("Successfully displayed", res)
        mock_canvas_service.visual.update_visual.assert_not_called()
        self.assertIsNone(tools.currently_displayed_image_path)

    def test_show_image_silently_rejects_narratron_avatar_alias_variations(self) -> None:
        """Aliases, hyphenated names, and extensions for narratron_avatar are all silently rejected."""
        mock_canvas_service = MagicMock()
        mock_canvas_service.visual.pinned = False
        tools = self.make_image_tools(
            self.config,
            theater_id="avatar_variations_test",
            theater_manager=self.manager,
            canvas_state_service=mock_canvas_service,
        )
        avatar_ref = os.path.join(tools.reference_dir, "narratron_avatar.jpg")
        Image.new("RGB", (20, 20), color="blue").save(avatar_ref)
        tools._load_references()

        variations = [
            "narratron_avatar",
            "narratron_avatar.jpg",
            "narratron-avatar",
            "narratron-avatar.png",
            "narratron avatar",
        ]
        for name in variations:
            res = tools.show_image(name)
            self.assertIn("Successfully displayed", res)
            mock_canvas_service.visual.update_visual.assert_not_called()
            self.assertIsNone(tools.currently_displayed_image_path)

    def test_show_image_silently_rejects_narratron_avatar_in_adventure_mode(self) -> None:
        """In adventure mode, silent rejection of narratron_avatar resets the story plan completed flag."""
        mock_canvas_service = MagicMock()
        mock_canvas_service.visual.pinned = False
        tools = self.make_image_tools(
            self.config,
            theater_id="avatar_adv_test",
            theater_manager=self.manager,
            canvas_state_service=mock_canvas_service,
            adventure_mode=True,
        )
        avatar_ref = os.path.join(tools.reference_dir, "narratron_avatar.jpg")
        Image.new("RGB", (20, 20), color="blue").save(avatar_ref)
        tools._load_references()

        tools.record_story_plan_completed()
        self.assertTrue(tools._story_plan_completed)

        res = tools.show_image("narratron_avatar")
        self.assertIn("Successfully displayed", res)
        mock_canvas_service.visual.update_visual.assert_not_called()
        self.assertIsNone(tools.currently_displayed_image_path)
        self.assertFalse(tools._story_plan_completed)

    @patch("tools.image.image_tool.get_image_provider")
    def test_create_image_populates_reference_image_names_in_metadata_and_shown_prompt(
        self, mock_get_provider
    ) -> None:
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        tools = self.make_image_tools(
            self.config,
            theater_id="ref_metadata_test",
            theater_manager=self.manager,
        )
        ref_path1 = os.path.join(tools.reference_dir, "hero.png")
        ref_path2 = os.path.join(tools.reference_dir, "castle.jpg")
        Image.new("RGB", (10, 10), color="red").save(ref_path1)
        Image.new("RGB", (10, 10), color="blue").save(ref_path2)
        tools._load_references()

        tools.create_image(
            "A warrior outside a castle with <hero> and <castle>",
            image_name="warrior_castle",
            display=True,
        )
        tools.join_generation()

        # Canvas visual state has shown_image_prompt updated with references
        visual = tools.canvas_manager.visual
        self.assertIsNotNone(visual)
        self.assertIn("A warrior outside a castle", visual.shown_image_prompt)
        self.assertIn("References: hero.png, castle.jpg", visual.shown_image_prompt)

        # payload() includes the updated prompt for the canvas hover button
        payload_prompt = str(visual.payload().get("prompt") or "")
        self.assertIn("A warrior outside a castle", payload_prompt)
        self.assertIn("References: hero.png, castle.jpg", payload_prompt)

        # Full-quality image on disk has embedded EXIF metadata with references
        full_quality_path = str(next(Path(tools.output_dir).glob("warrior_castle_*.jpg")))
        from utils.image_utils import extract_image_prompt
        full_meta = extract_image_prompt(full_quality_path)
        self.assertIn("A warrior outside a castle", full_meta)
        self.assertIn("References: hero.png, castle.jpg", full_meta)

        # Compressed WebP image on disk also has embedded EXIF metadata with references
        webp_path = os.path.splitext(full_quality_path)[0] + ".webp"
        webp_meta = extract_image_prompt(webp_path)
        self.assertIn("A warrior outside a castle", webp_meta)
        self.assertIn("References: hero.png, castle.jpg", webp_meta)

    def test_character_images_in_characters_dir_not_visible_in_overall_image_tool(self) -> None:
        tools = self.make_image_tools(self.config, theater_id="char_vis_test", theater_manager=self.manager)
        theater = self.manager.theater("char_vis_test")

        # 1. Scenery image in output/artifacts/images (should be visible)
        scene_img = os.path.join(tools.output_dir, "ancient_forest.png")
        Image.new("RGB", (10, 10), color="green").save(scene_img)

        # 2. Reference image in references/ (should be visible)
        ref_img = os.path.join(tools.reference_dir, "tavern_interior.png")
        Image.new("RGB", (10, 10), color="brown").save(ref_img)
        tools._load_references()

        # 3. Character portrait under references/characters/<Name>/
        char_dir = os.path.join(str(theater.characters_dir()), "Soran")
        os.makedirs(char_dir, exist_ok=True)
        char_img = os.path.join(char_dir, "1.png")
        Image.new("RGB", (10, 10), color="purple").save(char_img)
        tools._load_references()
        self.assertNotIn(char_img, [entry["path"] for entry in tools.list_references()])

        # Character image must not be in browse_images
        browsed = tools.browse_images()
        self.assertIn(scene_img, browsed)
        self.assertIn(ref_img, browsed)
        self.assertNotIn(char_img, browsed)

        # Character image must not be in search_image_by_metadata
        searched = tools.search_image_by_metadata("Soran")
        self.assertNotIn(char_img, searched)

        # Character image must not be in list_references
        ref_manifest_paths = [str(r.get("path") or "") for r in tools.list_references()]
        self.assertIn(ref_img, ref_manifest_paths)
        self.assertNotIn(char_img, ref_manifest_paths)

        # show_image must reject displaying character images
        res_alias = tools.show_image("Soran")
        self.assertIn("Error: Image 'Soran' not found.", res_alias)

        res_path = tools.show_image(char_img)
        self.assertIn("requires a reference handle", res_path)

        # Canvas visual state must not have displayed the character image
        self.assertNotEqual(tools.currently_displayed_image_path, char_img)

    def test_show_image_rejects_canvas_captures(self) -> None:
        tools = self.make_image_tools(self.config, theater_id="capture_test", theater_manager=self.manager)
        captures_dir = self.manager.theater("capture_test").canvas_captures_dir()
        captures_dir.mkdir(parents=True, exist_ok=True)
        capture = captures_dir / "canvas_deadbeef.png"
        Image.new("RGB", (10, 10), color="red").save(capture)

        tools.visual.register_image(str(capture), "canvas_capture")
        res = tools.show_image(reference_handle="canvas_capture")

        self.assertIn("is a canvas capture and cannot be displayed", res)
        self.assertIsNone(tools.currently_displayed_image_path)
        self.assertIsNone(tools.visual.shown_image_path)

