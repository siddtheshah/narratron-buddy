import io
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from PIL import Image

from testing.base import BaseTestCase
from tools.image_tool import ImageTools


def create_fake_image_bytes() -> bytes:
    img = Image.new("RGB", (10, 10), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestImageTools(BaseTestCase):
    def setUp(self):
        super().setUp()
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
    def test_init_theater_paths(self, mock_genai_client):
        theater_id = "test_theater_abc"
        tools = ImageTools(self.config, theater_id=theater_id)
        self.assertEqual(tools.active_theater_id, theater_id)
        self.assertTrue(tools.output_dir.endswith(os.path.join("theaters", theater_id, "output", "artifacts", "images")))
        self.assertTrue(tools.reference_dir.endswith(os.path.join("theaters", theater_id, "references")))
        self.assertEqual(tools.get_effective_output_dir(), tools.output_dir)

    @patch("tools.image_tool.genai.Client")
    def test_client_caching(self, mock_genai_client):
        mock_client_inst = MagicMock()
        mock_genai_client.return_value = mock_client_inst

        tools1 = ImageTools(self.config, theater_id="theater_1")
        tools2 = ImageTools(self.config, theater_id="theater_2")

        mock_genai_client.assert_called_once()
        self.assertIs(tools1.client, tools2.client)

    @patch("tools.image_tool.genai.Client")
    def test_create_image_marks_canvas_drawing_for_the_duration_of_the_call(self, mock_genai_client):
        canvas_state_service = MagicMock()
        tools = ImageTools(
            self.config,
            theater_id="drawing_indicator",
            canvas_state_service=canvas_state_service,
        )
        tools.last_create_time = 0.0
        tools._schedule_cooldown_timer = MagicMock()

        tools.create_image("a misty forest")

        self.assertEqual(
            canvas_state_service.set_tool_activity.call_args_list,
            [
                call("image", active=True, theater_id="drawing_indicator"),
                call("image", active=False, theater_id="drawing_indicator"),
            ],
        )

    @patch("tools.image_tool.genai.Client")
    def test_default_style_is_loaded_and_appended_only_when_needed(self, mock_genai_client):
        theater_id = "theater_default_style"
        theater_dir = Path(__file__).resolve().parent.parent / "theaters" / theater_id
        theater_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, theater_dir, True)
        (theater_dir / "style.txt").write_text("moody watercolor", encoding="utf-8")

        mock_part = MagicMock()
        mock_part.inline_data.data = create_fake_image_bytes()
        mock_genai_client.return_value.models.generate_content.return_value.candidates = [
            MagicMock(content=MagicMock(parts=[mock_part]))
        ]
        tools = ImageTools(self.config, theater_id=theater_id)
        tools.output_dir = self.temp_dir

        tools.create_image("a moonlit harbor", display=False)
        generated_prompt = mock_genai_client.return_value.models.generate_content.call_args.kwargs["contents"][-1]
        self.assertIn("Style: moody watercolor", generated_prompt)

        tools.last_create_time = 0
        tools.create_image("a moonlit harbor in a noir style", display=False)
        generated_prompt = mock_genai_client.return_value.models.generate_content.call_args.kwargs["contents"][-1]
        self.assertEqual(generated_prompt, "a moonlit harbor in a noir style")

    @patch("tools.image_tool.genai.Client")
    def test_references_loading_and_caching(self, mock_genai_client):
        theater_id = "theater_ref_test"
        tools = ImageTools(self.config, theater_id=theater_id)

        ref_path = os.path.join(tools.reference_dir, "hero_character.png")
        img = Image.new("RGB", (10, 10), color="red")
        img.save(ref_path)

        tools._load_references()
        manifest = tools.list_references()
        self.assertEqual(len(manifest), 1)
        self.assertEqual(manifest[0]["name"], "hero_character")
        self.assertEqual(manifest[0]["alias"], "hero_character")

        tools2 = ImageTools(self.config, theater_id=theater_id)
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

        tools = ImageTools(self.config, theater_id="test_theater")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)

        callback = MagicMock()
        tools.on_show_image = callback

        res = tools.create_image("sunset scene", image_name="sunset_01")
        self.assertIn("Successfully generated and displayed image", res)
        self.assertIn("sunset_01", res)
        callback.assert_called_once()
        self.assertIn("sunset_01", tools.image_aliases)
        self.assertTrue(os.path.exists(tools.image_aliases["sunset_01"]))

    @patch("tools.image_tool.genai.Client")
    def test_create_image_display_false(self, mock_genai_client):
        mock_part = MagicMock()
        mock_part.inline_data.data = create_fake_image_bytes()

        mock_response = MagicMock()
        mock_response.candidates = [
            MagicMock(content=MagicMock(parts=[mock_part]))
        ]

        mock_client_instance = MagicMock()
        mock_client_instance.models.generate_content.return_value = mock_response
        mock_genai_client.return_value = mock_client_instance

        tools = ImageTools(self.config, theater_id="test_theater")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)

        callback = MagicMock()
        tools.on_show_image = callback

        res = tools.create_image("sunset scene", image_name="sunset_02", display=False)
        self.assertIn("Successfully generated image", res)
        self.assertNotIn("and displayed", res)
        callback.assert_not_called()
        self.assertIn("sunset_02", tools.image_aliases)

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

        tools = ImageTools(self.config, theater_id="test_theater")
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
        tools = ImageTools(self.config, theater_id="test_theater")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        tools.reference_dir = os.path.join(self.temp_dir, "refs")
        os.makedirs(tools.output_dir, exist_ok=True)
        os.makedirs(tools.reference_dir, exist_ok=True)

        res = tools.create_image("a fantasy castle", "castle description", reference_images="nonexistent_ref")
        self.assertIn("Error: Reference image 'nonexistent_ref' not found.", res)

    @patch("tools.image_tool.genai.Client")
    def test_independent_cooldowns(self, mock_genai_client):
        tools = ImageTools(self.config, theater_id="test_theater")
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
        tools = ImageTools(self.config, theater_id="test_theater")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)
        callback = MagicMock()
        tools.on_show_image = callback

        file_path = os.path.join(tools.output_dir, "test.jpg")
        img = Image.new("RGB", (10, 10), color="green")
        img.save(file_path)

        res = tools.show_image(file_path)
        self.assertIn("Successfully displayed", res)
        callback.assert_called_once_with(file_path, transition="crossfade", effect="gleam3")

        res2 = tools.show_image(file_path)
        self.assertIn("show_image is on cooldown", res2)

    @patch("tools.image_tool.genai.Client")
    def test_show_image_transition(self, mock_genai_client):
        """Test that show_image forwards the transition parameter to the callback."""
        tools = ImageTools(self.config, theater_id="test_theater")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)
        callback = MagicMock()
        tools.on_show_image = callback

        file_path = os.path.join(tools.output_dir, "trans_test.jpg")
        img = Image.new("RGB", (10, 10), color="blue")
        img.save(file_path)

        res = tools.show_image(file_path, transition="zoom")
        self.assertIn("Successfully displayed", res)
        callback.assert_called_once_with(file_path, transition="zoom", effect="gleam3")

    @patch("tools.image_tool.genai.Client")
    def test_search_and_browse_images(self, mock_genai_client):
        tools = ImageTools(self.config, theater_id="test_theater")
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

        tools = ImageTools(self.config, theater_id="test_theater")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)

        # Test Case 1
        mock_client_instance.models.generate_content.return_value = mock_response_none_content
        res1 = tools.create_image("prompt 1", "desc 1")
        self.assertIn("Failed to generate image", res1)
        self.assertIn("SAFETY", res1)

        # Test Case 2
        mock_client_instance.models.generate_content.return_value = mock_response_none_parts
        tools.last_create_time = 0.0
        res2 = tools.create_image("prompt 2", "desc 2")
        self.assertIn("Failed to generate image", res2)

        # Test Case 3
        mock_client_instance.models.generate_content.return_value = mock_response_text_part
        tools.last_create_time = 0.0
        res3 = tools.create_image("prompt 3", "desc 3")
        self.assertIn("Failed to generate image", res3)
        self.assertIn("Model refusal message", res3)

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

        tools = ImageTools(self.config, theater_id="test_theater")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)

        callback = MagicMock()
        tools.on_show_image = callback

        res = tools.create_image("sunset scene", image_name="sunset_01")
        self.assertIn("Successfully generated and displayed image", res)
        self.assertIn("sunset_01", res)
        callback.assert_called_once()
        self.assertIn("sunset_01", tools.image_aliases)
        self.assertTrue(os.path.exists(tools.image_aliases["sunset_01"]))

    @patch("tools.image_tool.genai.Client")
    def test_create_image_display_false(self, mock_genai_client):
        mock_part = MagicMock()
        mock_part.inline_data.data = create_fake_image_bytes()

        mock_response = MagicMock()
        mock_response.candidates = [
            MagicMock(content=MagicMock(parts=[mock_part]))
        ]

        mock_client_instance = MagicMock()
        mock_client_instance.models.generate_content.return_value = mock_response
        mock_genai_client.return_value = mock_client_instance

        tools = ImageTools(self.config, theater_id="test_theater")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)

        callback = MagicMock()
        tools.on_show_image = callback

        res = tools.create_image("sunset scene", image_name="sunset_02", display=False)
        self.assertIn("Successfully generated image", res)
        self.assertNotIn("and displayed", res)
        callback.assert_not_called()
        self.assertIn("sunset_02", tools.image_aliases)

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

        tools = ImageTools(self.config, theater_id="test_theater")
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

    @patch("tools.image_tool.types.Part")
    @patch("tools.image_tool.genai.Client")
    def test_create_image_model_selection(self, mock_genai_client, mock_part_cls):
        mock_part_data = MagicMock()
        mock_part_data.inline_data.data = create_fake_image_bytes()
        mock_response = MagicMock()
        mock_response.candidates = [MagicMock(content=MagicMock(parts=[mock_part_data]))]

        mock_client_instance = MagicMock()
        mock_client_instance.models.generate_content.return_value = mock_response
        mock_genai_client.return_value = mock_client_instance

        tools = ImageTools(self.config, theater_id="test_theater")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        tools.reference_dir = os.path.join(self.temp_dir, "refs")
        os.makedirs(tools.output_dir, exist_ok=True)
        os.makedirs(tools.reference_dir, exist_ok=True)

        ref_file = os.path.join(tools.reference_dir, "ref1.png")
        Image.new("RGB", (10, 10), color="blue").save(ref_file)
        tools._load_references()

        # 1. Simple render without reference image -> uses simple_model (gemini-3.1-flash-lite-image)
        tools.create_image("a simple landscape")
        mock_client_instance.models.generate_content.assert_called_with(
            model="gemini-3.1-flash-lite-image",
            contents=["a simple landscape"],
        )

        # Reset cooldown & mock
        tools.last_create_time = 0.0
        mock_client_instance.models.generate_content.reset_mock()

        # 2. Render with reference image -> uses reference_model (gemini-3.1-flash-image)
        tools.create_image("a castle in style of ref1", reference_images="ref1")
        self.assertEqual(
            mock_client_instance.models.generate_content.call_args.kwargs["model"],
            "gemini-3.1-flash-image"
        )

    @patch("tools.image_tool.genai.Client")
    def test_create_image_with_missing_reference_fails(self, mock_genai_client):
        tools = ImageTools(self.config, theater_id="test_theater")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        tools.reference_dir = os.path.join(self.temp_dir, "refs")
        os.makedirs(tools.output_dir, exist_ok=True)
        os.makedirs(tools.reference_dir, exist_ok=True)

        res = tools.create_image("a fantasy castle", reference_images="nonexistent_ref")
        self.assertIn("Error: Reference image 'nonexistent_ref' not found.", res)

    @patch("tools.image_tool.genai.Client")
    def test_independent_cooldowns(self, mock_genai_client):
        tools = ImageTools(self.config, theater_id="test_theater")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)
        callback = MagicMock()
        tools.on_show_image = callback

        file_path = os.path.join(tools.output_dir, "test.jpg")
        img = Image.new("RGB", (10, 10), color="green")
        img.save(file_path)

        tools.last_create_time = time.time()
        self.assertIn("create_image is on cooldown", tools.create_image("prompt"))

        res = tools.show_image(file_path)
        self.assertIn("Successfully displayed", res)

    @patch("tools.image_tool.genai.Client")
    def test_show_image_and_cooldown(self, mock_genai_client):
        tools = ImageTools(self.config, theater_id="test_theater")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)
        callback = MagicMock()
        tools.on_show_image = callback

        file_path = os.path.join(tools.output_dir, "test.jpg")
        img = Image.new("RGB", (10, 10), color="green")
        img.save(file_path)

        res = tools.show_image(file_path)
        self.assertIn("Successfully displayed", res)
        callback.assert_called_once_with(file_path, transition="crossfade", effect="gleam3")

        res2 = tools.show_image(file_path)
        self.assertIn("show_image is on cooldown", res2)

    @patch("tools.image_tool.genai.Client")
    def test_show_image_transition(self, mock_genai_client):
        """Test that show_image forwards the transition parameter to the callback."""
        tools = ImageTools(self.config, theater_id="test_theater")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)
        callback = MagicMock()
        tools.on_show_image = callback

        file_path = os.path.join(tools.output_dir, "trans_test.jpg")
        img = Image.new("RGB", (10, 10), color="blue")
        img.save(file_path)

        res = tools.show_image(file_path, transition="zoom")
        self.assertIn("Successfully displayed", res)
        callback.assert_called_once_with(file_path, transition="zoom", effect="gleam3")

    @patch("tools.image_tool.genai.Client")
    def test_search_and_browse_images(self, mock_genai_client):
        tools = ImageTools(self.config, theater_id="test_theater")
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

        tools = ImageTools(self.config, theater_id="test_theater")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)

        # Test Case 1
        mock_client_instance.models.generate_content.return_value = mock_response_none_content
        res1 = tools.create_image("prompt 1")
        self.assertIn("Failed to generate image", res1)
        self.assertIn("SAFETY", res1)

        # Test Case 2
        mock_client_instance.models.generate_content.return_value = mock_response_none_parts
        tools.last_create_time = 0.0
        res2 = tools.create_image("prompt 2")
        self.assertIn("Failed to generate image", res2)

        # Test Case 3
        mock_client_instance.models.generate_content.return_value = mock_response_text_part
        tools.last_create_time = 0.0
        res3 = tools.create_image("prompt 3")
        self.assertIn("Failed to generate image", res3)
        self.assertIn("Model refusal message", res3)

    @patch("tools.image_tool.genai.Client")
    def test_cooldown_expired_callbacks(self, mock_genai_client):
        tools = ImageTools(self.config, theater_id="test_theater")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        tools.cooldown_duration = 0.1
        os.makedirs(tools.output_dir, exist_ok=True)

        on_cooldown_expired = MagicMock()
        tools.on_cooldown_expired = on_cooldown_expired

        file_path = os.path.join(tools.output_dir, "test.jpg")
        img = Image.new("RGB", (10, 10), color="purple")
        img.save(file_path)

        res1 = tools.show_image(file_path)
        self.assertIn("Successfully displayed", res1)

        # Do NOT call show_image again. Verify callback fires automatically after cooldown duration.
        time.sleep(0.25)
        on_cooldown_expired.assert_called_with("show_image")

    @patch("tools.image_tool.genai.Client")
    def test_on_after_tool_call_and_canvas_info(self, mock_genai_client):
        tools = ImageTools(self.config, theater_id="test_theater")
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)

        after_tool_cb = MagicMock()
        tools.on_after_tool_call = after_tool_cb

        file_path = os.path.join(tools.output_dir, "view_test.jpg")
        img = Image.new("RGB", (10, 10), color="blue")
        img.save(file_path)

        res = tools.show_image(file_path, transition="fade")
        self.assertIn("Successfully displayed", res)

        after_tool_cb.assert_called_once()
        tool_name, canvas_info = after_tool_cb.call_args[0]
        self.assertEqual(tool_name, "show_image")
        self.assertEqual(canvas_info["path"], file_path)
        self.assertEqual(canvas_info["transition"], "fade")

        info = tools.get_current_canvas_image_info()
        self.assertEqual(info["path"], file_path)
        self.assertEqual(info["transition"], "fade")

    @patch("tools.image_tool.genai.Client")
    def test_on_show_image_triggers_with_and_without_canvas_state_service(self, mock_genai_client):
        mock_canvas_service = MagicMock()
        tools = ImageTools(self.config, theater_id="test_theater", canvas_state_service=mock_canvas_service)
        tools.output_dir = os.path.join(self.temp_dir, "output")
        os.makedirs(tools.output_dir, exist_ok=True)

        on_show_image_cb = MagicMock()
        tools.on_show_image = on_show_image_cb

        file_path = os.path.join(tools.output_dir, "view_test_cb.jpg")
        img = Image.new("RGB", (10, 10), color="green")
        img.save(file_path)

        res = tools.show_image(file_path, transition="crossfade", effect="gleam3")
        self.assertIn("Successfully displayed", res)

        # Verify canvas_state_service.show_image was called
        mock_canvas_service.show_image.assert_called_once_with(
            file_path, theater_id="test_theater", transition="crossfade", effect="gleam3"
        )
        # Verify on_show_image callback was ALSO triggered
        on_show_image_cb.assert_called_once_with(
            file_path, transition="crossfade", effect="gleam3"
        )


if __name__ == "__main__":
    unittest.main()

