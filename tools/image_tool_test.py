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

        tools.create_image("a dog carrying a bag", display=False)
        tools.join_generation()

        mock_get_provider.assert_called_once_with(
            "hybrid-flux-gemini", {"classifier_model": "gemini-2.5-flash-lite"}
        )
        request = provider.generate.call_args.args[0]
        self.assertEqual(request.prompt, "a dog carrying a bag")
        self.assertEqual(request.references, [])

    @patch("tools.image_tool.get_image_provider")
    def test_create_image_passes_loaded_references_to_provider(self, mock_get_provider):
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        tools = ImageTools(self.config, theater_id="references", theater_manager=self.manager)
        reference_path = os.path.join(tools.reference_dir, "hero.png")
        Image.new("RGB", (10, 10), color="red").save(reference_path)
        tools._load_references()

        tools.create_image("hero at dawn", reference_images="hero", display=False)
        tools.join_generation()

        references = provider.generate.call_args.args[0].references
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].name, "hero.png")
        self.assertEqual(references[0].mime_type, "image/png")

    @patch("tools.image_tool.get_image_provider")
    def test_create_image_saves_output_and_registers_alias(self, mock_get_provider):
        provider = mock_get_provider.return_value
        provider.generate.return_value = self._provider_result()
        tools = ImageTools(self.config, theater_id="alias", theater_manager=self.manager)
        created = MagicMock()
        tools.on_image_created = created

        tools.create_image("sunset scene", image_name="sunset_01", display=False)
        tools.join_generation()

        self.assertTrue(os.path.exists(tools.image_aliases["sunset_01"]))
        created.assert_called_once_with(tools.image_aliases["sunset_01"])

    def test_create_image_requires_a_provider(self):
        with self.assertRaisesRegex(ValueError, "image_generation.provider"):
            ImageTools({"image_generation": {"cooldown_duration": 0}}, "missing", self.manager)

    @patch("tools.image_tool.get_image_provider")
    def test_missing_reference_returns_error_without_calling_provider(self, mock_get_provider):
        tools = ImageTools(self.config, theater_id="missing_reference", theater_manager=self.manager)

        result = tools.create_image("a castle", reference_images="not-here")

        self.assertIn("Reference image 'not-here' not found", result)
        mock_get_provider.assert_not_called()
