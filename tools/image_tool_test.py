import io
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

from PIL import Image

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

        tools.create_image("a dog carrying a bag", display=False)
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

        tools.create_image("hero at dawn", reference_images="hero", display=False)
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
        tools.create_image("a shining diamond", display=True)
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

        result = tools.create_image("a castle", reference_images="not-here")

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
