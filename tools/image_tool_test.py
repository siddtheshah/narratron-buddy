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
        self.temp_dir = tempfile.mkdtemp()
        self.output_dir = os.path.join(self.temp_dir, "images")
        self.ref_dir = os.path.join(self.temp_dir, "ref_library")
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.ref_dir, exist_ok=True)

        self.config = {
            "image_generation": {
                "output_folder": self.output_dir,
                "reference_library_folder": self.ref_dir,
                "cooldown_duration": 5.0,
            },
            "gcloud": {
                "project_id": "test-project"
            }
        }

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("tools.image_tool.genai.Client")
    def test_init_and_reference_library(self, mock_genai_client):
        ref_path = os.path.join(self.ref_dir, "hero.png")
        img = Image.new("RGB", (10, 10), color="red")
        img.save(ref_path)

        tools = ImageTools(self.config)
        library = tools.list_reference_library()
        self.assertEqual(len(library), 1)
        self.assertEqual(library[0]["name"], "hero")

    @patch("tools.image_tool.genai.Client")
    def test_create_image_success(self, mock_genai_client):
        mock_part = MagicMock()
        mock_part.inline_data.data = create_fake_image_bytes()
        
        mock_response = MagicMock()
        mock_response.candidates = [
            MagicMock(content=MagicMock(parts=[mock_part]))
        ]
        
        mock_client_instance = MagicMock()
        mock_client_instance.models.generate_content.return_value = mock_response
        mock_genai_client.return_value = mock_client_instance

        tools = ImageTools(self.config)
        callback = MagicMock()
        tools.on_show_image = callback

        res = tools.create_image("sunset scene", "golden hours sunset", image_name="sunset_01")
        self.assertIn("Successfully generated and displayed image", res)
        self.assertIn("sunset_01", res)
        callback.assert_called_once()
        self.assertIn("sunset_01", tools.image_aliases)

    @patch("tools.image_tool.genai.Client")
    def test_independent_cooldowns(self, mock_genai_client):
        tools = ImageTools(self.config)
        callback = MagicMock()
        tools.on_show_image = callback

        file_path = os.path.join(self.output_dir, "test.jpg")
        img = Image.new("RGB", (10, 10), color="green")
        img.save(file_path)

        # Trigger create_image cooldown
        tools.last_create_time = time.time()
        self.assertIn("create_image is on cooldown", tools.create_image("prompt", "desc"))

        # show_image should still succeed as its cooldown is separate
        res = tools.show_image("test.jpg")
        self.assertIn("Successfully displayed", res)

    @patch("tools.image_tool.genai.Client")
    def test_show_image(self, mock_genai_client):
        tools = ImageTools(self.config)
        callback = MagicMock()
        tools.on_show_image = callback

        file_path = os.path.join(self.output_dir, "test.jpg")
        img = Image.new("RGB", (10, 10), color="green")
        img.save(file_path)

        res = tools.show_image("test.jpg")
        self.assertIn("Successfully displayed", res)
        callback.assert_called_once_with(file_path)

        res2 = tools.show_image("test.jpg")
        self.assertIn("show_image is on cooldown", res2)

    @patch("tools.image_tool.genai.Client")
    def test_search_and_browse_images(self, mock_genai_client):
        img_path = os.path.join(self.output_dir, "oasis_view.jpg")
        img = Image.new("RGB", (10, 10), color="yellow")
        img.save(img_path)

        tools = ImageTools(self.config)
        all_imgs = tools.browse_images()
        self.assertIn(img_path, all_imgs)

        matches = tools.search_image_by_metadata("oasis")
        self.assertIn(img_path, matches)

if __name__ == "__main__":
    unittest.main()
