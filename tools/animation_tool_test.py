import io
import os
import shutil
import tempfile
import re
import threading
from unittest.mock import MagicMock, patch

from PIL import Image

from components.theater_manager import TheaterManager
from components.canvas_state_service import CanvasStateService
from providers import ImageGenerationResult
from providers.fal_qwen_layered_provider import LayeredImageResult
from testing.base import BaseTestCase
from tools.animation_tool import AnimationTools
from tools.image_tool import ImageTools


def fake_image_bytes() -> bytes:
    image = Image.new("RGB", (10, 10), color="blue")
    output = io.BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


BASE_FRAME = "A lantern hangs in a quiet tavern."
SECOND_FRAME_CHANGE = "The lantern swings slightly to the left."
THIRD_FRAME_CHANGE = "The lantern swings back toward center."


class TestAnimationTools(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.manager = TheaterManager(base_theaters_dir=self.temp_dir)
        self.config = {"image_generation": {"cooldown_duration": 0, "provider": "gemini"}}

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("tools.image_tool.get_image_provider")
    def test_create_triframe_uses_the_injected_provider_and_saves_three_frames(self, mock_get_provider):
        image_provider = MagicMock()
        image_provider.generate.return_value = ImageGenerationResult(
            image_bytes=fake_image_bytes(), mime_type="image/jpeg", provider="fake", model="fake-model"
        )
        text_provider = MagicMock()
        text_provider.generate.return_value.parsed = {
            "base_frame": BASE_FRAME,
            "second_frame_change": SECOND_FRAME_CHANGE,
            "third_frame_change": THIRD_FRAME_CHANGE,
        }
        text_provider.generate.return_value.provider = "planner"
        text_provider.generate.return_value.model = "planner-model"
        text_provider.generate.return_value.request_id = "planner-1"
        text_provider.generate.return_value.usage = {"tokens": 10}

        image_tools = ImageTools(self.config, "tri_frame", self.manager)
        animation_tools = AnimationTools(image_tools, image_provider, text_provider, MagicMock())

        result = animation_tools.create_triframe("A lantern in a quiet tavern.", "lantern")
        animation_tools.join_generation()

        self.assertIn("started", result)
        animation_id = re.search(r"Animation ID: '([^']+)'", result).group(1)
        self.assertEqual(image_provider.generate.call_count, 3)
        self.assertEqual(text_provider.generate.call_count, 1)
        self.assertIn("Scene prompt:", text_provider.generate.call_args.args[0].prompt)
        self.assertTrue(os.path.exists(image_tools.image_aliases[f"{animation_id}_frame_1"]))
        self.assertTrue(os.path.exists(image_tools.image_aliases[f"{animation_id}_frame_2"]))
        self.assertTrue(os.path.exists(image_tools.image_aliases[f"{animation_id}_frame_3"]))
        self.assertEqual(
            os.path.basename(os.path.dirname(image_tools.image_aliases[f"{animation_id}_frame_1"])),
            animation_id,
        )
        first_prompt = image_provider.generate.call_args_list[0].args[0].prompt
        self.assertIn(BASE_FRAME, first_prompt)
        self.assertIn("Do not create a collage, triptych, storyboard", first_prompt)
        self.assertNotIn("three-frame", first_prompt)
        second_prompt = image_provider.generate.call_args_list[1].args[0].prompt
        third_prompt = image_provider.generate.call_args_list[2].args[0].prompt
        self.assertIn(SECOND_FRAME_CHANGE, second_prompt)
        self.assertIn(THIRD_FRAME_CHANGE, third_prompt)
        self.assertEqual(image_provider.generate.call_args_list[0].args[0].references, [])
        self.assertEqual(len(image_provider.generate.call_args_list[1].args[0].references), 1)
        self.assertEqual(len(image_provider.generate.call_args_list[2].args[0].references), 1)

    @patch("tools.image_tool.get_image_provider")
    def test_play_animation_publishes_saved_frames_to_canvas_state(self, mock_get_provider):
        image_provider = MagicMock()
        image_provider.generate.return_value = ImageGenerationResult(
            image_bytes=fake_image_bytes(), mime_type="image/jpeg", provider="fake", model="fake-model"
        )
        text_provider = MagicMock()
        text_provider.generate.return_value.parsed = {
            "base_frame": "A candle burns in a still room.",
            "second_frame_change": "The flame leans right.",
            "third_frame_change": "The flame returns upright.",
        }
        text_provider.generate.return_value.provider = "planner"
        text_provider.generate.return_value.model = "planner-model"
        text_provider.generate.return_value.request_id = "planner-1"
        text_provider.generate.return_value.usage = {}

        canvas_state_service = CanvasStateService(self.manager)
        image_tools = ImageTools(
            self.config, "tri_frame_canvas", self.manager, canvas_state_service=canvas_state_service
        )
        animation_tools = AnimationTools(image_tools, image_provider, text_provider, MagicMock())

        result = animation_tools.create_triframe("A candle burns in a still room.", "candle")
        animation_tools.join_generation()
        animation_id = re.search(r"Animation ID: '([^']+)'", result).group(1)

        self.assertNotIn("animation", canvas_state_service.latest_state("tri_frame_canvas"))
        self.assertIn("Playing", animation_tools.play_animation(animation_id))

        animation = canvas_state_service.latest_state("tri_frame_canvas")["animation"]
        self.assertEqual(animation["type"], "triframe")
        self.assertEqual(len(animation["frames"]), 3)
        self.assertIn(f"/output/animations/{animation_id}/frame_1.jpg", animation["frames"][0])

    @patch("tools.image_tool.get_image_provider")
    def test_create_triframe_passes_references_to_each_frame(self, mock_get_provider):
        image_provider = MagicMock()
        image_provider.generate.return_value = ImageGenerationResult(
            image_bytes=fake_image_bytes(), mime_type="image/jpeg", provider="fake", model="fake-model"
        )
        text_provider = MagicMock()
        text_provider.generate.return_value.parsed = {
            "base_frame": "A hero stands in a courtyard.",
            "second_frame_change": "The hero lifts a hand.",
            "third_frame_change": "The hero lowers the hand.",
        }
        text_provider.generate.return_value.provider = "planner"
        text_provider.generate.return_value.model = "planner-model"
        text_provider.generate.return_value.request_id = "planner-1"
        text_provider.generate.return_value.usage = {}

        image_tools = ImageTools(self.config, "tri_frame_references", self.manager)
        reference_path = os.path.join(image_tools.reference_dir, "hero.png")
        Image.new("RGB", (10, 10), color="red").save(reference_path)
        image_tools._load_references()
        animation_tools = AnimationTools(image_tools, image_provider, text_provider, MagicMock())

        animation_tools.create_triframe("A hero stands in a courtyard.", reference_images="hero")
        animation_tools.join_generation()

        requests = [call.args[0] for call in image_provider.generate.call_args_list]
        self.assertEqual(requests[0].references[0].name, "hero.png")
        self.assertEqual(len(requests[1].references), 2)
        self.assertEqual(requests[1].references[1].name, "hero.png")

    @patch("tools.image_tool.get_image_provider")
    def test_create_triframe_rejects_unknown_reference(self, mock_get_provider):
        image_tools = ImageTools(self.config, "tri_frame_missing_reference", self.manager)
        animation_tools = AnimationTools(image_tools, MagicMock(), MagicMock(), MagicMock())

        result = animation_tools.create_triframe("A hero stands in a courtyard.", reference_images="missing")

        self.assertIn("Reference image 'missing' not found", result)

    @patch("tools.image_tool.get_image_provider")
    def test_animation_cooldown_uses_animation_configuration(self, mock_get_provider):
        image_tools = ImageTools(self.config, "tri_frame_cooldown", self.manager)
        animation_tools = AnimationTools(
            image_tools, MagicMock(), MagicMock(), MagicMock(), {"enabled": True, "cooldown_duration": 27}
        )

        self.assertEqual(animation_tools.cooldown_duration, 27)

    @patch("tools.image_tool.get_image_provider")
    def test_animation_tools_requires_a_text_planner_dependency(self, mock_get_provider):
        image_tools = ImageTools(self.config, "layered_missing_planner", self.manager)
        with self.assertRaises(TypeError):
            AnimationTools(image_tools, MagicMock(), MagicMock())

    @patch("tools.image_tool.get_image_provider")
    def test_layered_animation_saves_grounded_manifest_and_publishes_layered_canvas_state(self, mock_get_provider):
        image_provider = MagicMock()
        image_provider.generate.return_value = ImageGenerationResult(
            image_bytes=fake_image_bytes(), mime_type="image/jpeg", provider="base", model="base-model"
        )
        layered_provider = MagicMock()
        layered_provider.model = "fal-ai/qwen-image-layered"
        layered_provider.decompose.return_value = LayeredImageResult(
            images=[(fake_image_bytes(), "image/png")] * 3, request_id="qwen-1", usage={"seed": 8}
        )
        planning_provider = MagicMock()
        planning_provider.generate.return_value.parsed = {
            "background": {"description": "distant sky and cliff", "effect": "none"},
            "subject": {"description": "the hero on the cliff", "effect": "pulse"},
            "foreground": {"description": "foreground leaves", "effect": "sway"},
        }
        planning_provider.generate.return_value.provider = "planner"
        planning_provider.generate.return_value.model = "planner-model"
        planning_provider.generate.return_value.request_id = "planner-1"
        planning_provider.generate.return_value.usage = {"tokens": 42}
        canvas_state_service = CanvasStateService(self.manager)
        image_tools = ImageTools(self.config, "layered_canvas", self.manager, canvas_state_service=canvas_state_service)
        tools = AnimationTools(image_tools, image_provider, planning_provider, layered_provider, {"cooldown_duration": 0})

        result = tools.create_layered_animation("A hero on a cliff with foreground leaves.", "cliff")
        tools.join_generation()
        animation_id = re.search(r"Animation ID: '([^']+)'", result).group(1)
        manifest_path = os.path.join(tools.animations_dir, animation_id, "animation.json")
        with open(manifest_path, encoding="utf-8") as stream:
            manifest = __import__("json").load(stream)
        self.assertEqual(len(manifest["layers"]), 3)
        self.assertEqual(manifest["layers"][-1]["effect"], "sway")
        self.assertEqual(manifest["provider"]["planner"]["request_id"], "planner-1")
        self.assertEqual(planning_provider.generate.call_args.args[0].temperature, 0.1)
        self.assertIsNotNone(planning_provider.generate.call_args.args[0].response_json_schema)
        self.assertIsNone(planning_provider.generate.call_args.args[0].response_schema)
        self.assertIn("Ground the decomposition", layered_provider.decompose.call_args.args[0].prompt)
        self.assertIn("foreground", image_provider.generate.call_args.args[0].prompt)

        self.assertIn("Playing layered", tools.play_layered_animation(animation_id))
        animation = canvas_state_service.latest_state("layered_canvas")["animation"]
        self.assertEqual(animation["type"], "layered")
        self.assertEqual(animation["layers"][-1]["effect"], "sway")

    @patch("tools.image_tool.get_image_provider")
    def test_layered_animation_is_single_flight_while_planning(self, mock_get_provider):
        started = threading.Event()
        release = threading.Event()
        planner = MagicMock()
        planner_result = MagicMock(parsed={
            "background": {"description": "night sky", "effect": "none"},
            "subject": {"description": "a traveler", "effect": "pulse"},
            "foreground": None,
        }, provider="planner", model="planner", request_id="p1", usage={})

        def plan(_request):
            started.set()
            release.wait(timeout=2)
            return planner_result

        planner.generate.side_effect = plan
        image_provider = MagicMock()
        image_provider.generate.return_value = ImageGenerationResult(fake_image_bytes(), "image/jpeg", "base", "base")
        layered_provider = MagicMock(model="fal-ai/qwen-image-layered")
        layered_provider.decompose.return_value = LayeredImageResult([(fake_image_bytes(), "image/png")] * 2)
        image_tools = ImageTools(self.config, "single_flight", self.manager)
        tools = AnimationTools(image_tools, image_provider, planner, layered_provider, {"cooldown_duration": 0})
        first_result = []
        first = threading.Thread(target=lambda: first_result.append(tools.create_layered_animation("A traveler under stars.")))
        first.start()
        self.assertTrue(started.wait(timeout=1))

        second_result = tools.create_layered_animation("A second scene.")
        self.assertIn("already being generated", second_result)
        release.set()
        first.join(timeout=2)
        tools.join_generation(timeout=2)
        self.assertIn("generation started", first_result[0])
        self.assertFalse(tools.is_in_flight("create_layered_animation"))

    def test_animation_layer_accepts_twist_and_bend_effects(self):
        from tools.animation_tool import AnimationLayer
        layer_twist = AnimationLayer(description="a spinning magic portal", effect="twist")
        self.assertEqual(layer_twist.effect, "twist")
        layer_bend = AnimationLayer(description="a flexing reed", effect="bend")
        self.assertEqual(layer_bend.effect, "bend")
        layer_rocking = AnimationLayer(description="a rocking boat", effect="gentle_rocking")
        self.assertEqual(layer_rocking.effect, "gentle_rocking")

    def test_plan_triframe_with_provider_passes_schema_and_instructions(self):
        text_provider = MagicMock()
        text_provider.generate.return_value.parsed = {
            "base_frame": BASE_FRAME,
            "second_frame_change": SECOND_FRAME_CHANGE,
            "third_frame_change": THIRD_FRAME_CHANGE,
        }
        text_provider.generate.return_value.provider = "test-provider"
        text_provider.generate.return_value.model = "test-model"
        text_provider.generate.return_value.request_id = "req-1"
        text_provider.generate.return_value.usage = {"prompt_tokens": 15}

        plan, debug = AnimationTools.plan_triframe_with_provider(text_provider, "A tavern lantern scene.")

        self.assertEqual(plan["base_frame"], BASE_FRAME)
        self.assertEqual(plan["second_frame_change"], SECOND_FRAME_CHANGE)
        self.assertEqual(plan["third_frame_change"], THIRD_FRAME_CHANGE)
        self.assertEqual(debug["provider"], "test-provider")
        request = text_provider.generate.call_args.args[0]
        self.assertIn("A tavern lantern scene.", request.prompt)
        self.assertIn("clear action difference", request.prompt)
        self.assertIsNotNone(request.response_json_schema)

