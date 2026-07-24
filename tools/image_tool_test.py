import io
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from tools.image_tool import ImageTools


def create_fake_image_bytes() -> bytes:
    img = Image.new("RGB", (10, 10), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestImageTools(unittest.TestCase):
    def setUp(self):
        ImageTools._client_cache = None
        ImageTools._references_cache = {}
        ImageTools._reference_dir_cached = None

        self.temp_dir = tempfile.mkdtemp()
        self.config = {
            "image_generation": {
                "cooldown_duration": 5.0,
            },
            "gcloud": {
                "project_id": "test-project"
            }
        }

    def tearDown(self):
        ImageTools._client_cache = None
        ImageTools._references_cache = {}
        ImageTools._reference_dir_cached = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("tools.image_tool.genai.Client")
    def test_init_session_paths(self, mock_genai_client):
        session_id = "test_session_abc"
        tools = ImageTools(self.config, session_id=session_id)
        self.assertEqual(tools.active_session_id, session_id)
        self.assertTrue(tools.output_dir.endswith(os.path.join("sessions", session_id, "output", "artifacts", "images")))
        self.assertTrue(tools.reference_dir.endswith(os.path.join("sessions", session_id, "references")))
        self.assertEqual(tools.get_effective_output_dir(), tools.output_dir)

    @patch("tools.image_tool.genai.Client")
    def test_client_caching(self, mock_genai_client):
        mock_client_inst = MagicMock()
        mock_genai_client.return_value = mock_client_inst

        tools1 = ImageTools(self.config, session_id="session_1")
        tools2 = ImageTools(self.config, session_id="session_2")

        mock_genai_client.assert_called_once()
        self.assertIs(tools1.client, tools2.client)

    @patch("tools.image_tool.genai.Client")
    def test_references_loading_and_caching(self, mock_genai_client):
        session_id = "session_ref_test"
        tools = ImageTools(self.config, session_id=session_id)

        ref_path = os.path.join(tools.reference_dir, "hero_character.png")
        img = Image.new("RGB", (10, 10), color="red")
        img.save(ref_path)

        tools._load_references()
        manifest = tools.list_references()
        self.assertEqual(len(manifest), 1)
        self.assertEqual(manifest[0]["name"], "hero_character")
        self.assertEqual(manifest[0]["alias"], "hero_character")

        tools2 = ImageTools(self.config, session_id=session_id)
        self.assertEqual(len(tools2.list_references()), 1)

    @patch("tools.image_tool.genai.Client")
    def test_create_image_success_and_alias(self, mock_genai_client):
        mock_part = MagicMock()
        mock_part.inline_data.data = create_fake_image_bytes()

        mock_response = MagicMock()
        mock_response.candidates = [
            MagicMock(content=MagicMock(parts=[mock_part]))
        ]

        mock_client_instance = MagicMock()
        mock_client_instance.models.generate_content.return_value = mock_response
        mock_genai_client.return_value = mock_client_instance

        tools = ImageTools(self.config, session_id="test_session")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)

        callback = MagicMock()
        tools.on_show_image = callback

        res = tools.create_image("sunset scene", "golden hours sunset", image_name="sunset_01")
        self.assertIn("Successfully generated and displayed image", res)
        self.assertIn("sunset_01", res)
        callback.assert_called_once()
        self.assertIn("sunset_01", tools.image_aliases)
        self.assertTrue(os.path.exists(tools.image_aliases["sunset_01"]))

    @patch("tools.image_tool.types.Part")
    @patch("tools.image_tool.genai.Client")
    def test_create_image_with_reference_images(self, mock_genai_client, mock_part_cls):
        mock_part_data = MagicMock()
        mock_part_data.inline_data.data = create_fake_image_bytes()
        mock_response = MagicMock()
        mock_response.candidates = [MagicMock(content=MagicMock(parts=[mock_part_data]))]

        mock_client_instance = MagicMock()
        mock_client_instance.models.generate_content.return_value = mock_response
        mock_genai_client.return_value = mock_client_instance

        tools = ImageTools(self.config, session_id="test_session")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        tools.reference_dir = os.path.join(self.temp_dir, "refs")
        os.makedirs(tools.output_dir, exist_ok=True)
        os.makedirs(tools.reference_dir, exist_ok=True)

        ref_file = os.path.join(tools.reference_dir, "style_ref.png")
        Image.new("RGB", (10, 10), color="blue").save(ref_file)
        tools._load_references()

        res = tools.create_image("a fantasy castle", "castle description", reference_images="style_ref")
        self.assertIn("Successfully generated and displayed image", res)
        mock_part_cls.from_bytes.assert_called_once()

    @patch("tools.image_tool.genai.Client")
    def test_create_image_with_missing_reference_fails(self, mock_genai_client):
        tools = ImageTools(self.config, session_id="test_session")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        tools.reference_dir = os.path.join(self.temp_dir, "refs")
        os.makedirs(tools.output_dir, exist_ok=True)
        os.makedirs(tools.reference_dir, exist_ok=True)

        res = tools.create_image("a fantasy castle", "castle description", reference_images="nonexistent_ref")
        self.assertIn("Error: Reference image 'nonexistent_ref' not found.", res)

    @patch("tools.image_tool.genai.Client")
    def test_independent_cooldowns(self, mock_genai_client):
        tools = ImageTools(self.config, session_id="test_session")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)
        callback = MagicMock()
        tools.on_show_image = callback

        file_path = os.path.join(tools.output_dir, "test.jpg")
        img = Image.new("RGB", (10, 10), color="green")
        img.save(file_path)

        tools.last_create_time = time.time()
        self.assertIn("create_image is on cooldown", tools.create_image("prompt", "desc"))

        res = tools.show_image(file_path)
        self.assertIn("Successfully displayed", res)

    @patch("tools.image_tool.genai.Client")
    def test_show_image_and_cooldown(self, mock_genai_client):
        tools = ImageTools(self.config, session_id="test_session")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)
        callback = MagicMock()
        tools.on_show_image = callback

        file_path = os.path.join(tools.output_dir, "test.jpg")
        img = Image.new("RGB", (10, 10), color="green")
        img.save(file_path)

        res = tools.show_image(file_path)
        self.assertIn("Successfully displayed", res)
        callback.assert_called_once_with(file_path)

        res2 = tools.show_image(file_path)
        self.assertIn("show_image is on cooldown", res2)

    @patch("tools.image_tool.genai.Client")
    def test_search_and_browse_images(self, mock_genai_client):
        tools = ImageTools(self.config, session_id="test_session")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)

        img_path = os.path.join(tools.output_dir, "oasis_view.jpg")
        img = Image.new("RGB", (10, 10), color="yellow")
        img.save(img_path)

        all_imgs = tools.browse_images()
        self.assertIn(img_path, all_imgs)

        matches = tools.search_image_by_metadata("oasis")
        self.assertIn(img_path, matches)

    @patch("tools.image_tool.genai.Client")
    def test_create_image_handles_none_content_or_parts(self, mock_genai_client):
        # Case 1: Candidate with content=None
        mock_response_none_content = MagicMock()
        mock_response_none_content.candidates = [MagicMock(content=None, finish_reason="SAFETY")]

        # Case 2: Candidate with content.parts=None
        mock_response_none_parts = MagicMock()
        mock_response_none_parts.candidates = [MagicMock(content=MagicMock(parts=None), finish_reason="SAFETY")]

        # Case 3: Candidate with text part instead of image data
        text_part = MagicMock(spec=["text"], text="Model refusal message")
        mock_response_text_part = MagicMock()
        mock_response_text_part.candidates = [MagicMock(content=MagicMock(parts=[text_part]))]

        mock_client_instance = MagicMock()
        mock_genai_client.return_value = mock_client_instance

        tools = ImageTools(self.config, session_id="test_session")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)

        # Test Case 1
        mock_client_instance.models.generate_content.return_value = mock_response_none_content
        res1 = tools.create_image("prompt 1", "desc 1")
        self.assertIn("Failed to generate image", res1)
        self.assertIn("SAFETY", res1)

        # Reset cooldown for next call
        tools.last_create_time = 0.0

        # Test Case 2
        mock_client_instance.models.generate_content.return_value = mock_response_none_parts
        res2 = tools.create_image("prompt 2", "desc 2")
        self.assertIn("Failed to generate image", res2)

        # Reset cooldown for next call
        tools.last_create_time = 0.0

        # Test Case 3
        mock_client_instance.models.generate_content.return_value = mock_response_text_part
        res3 = tools.create_image("prompt 3", "desc 3")
        self.assertIn("Failed to generate image", res3)
        self.assertIn("Model refusal message", res3)


if __name__ == "__main__":
    unittest.main()


