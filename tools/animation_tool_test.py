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
    def test_create_animation_uses_triframe_technique(self, mock_get_provider):
        image_provider = MagicMock()
        image_provider.generate.return_value = ImageGenerationResult(
            image_bytes=fake_image_bytes(), mime_type="image/jpeg", provider="fake", model="fake-model"
        )
        text_provider = MagicMock()
        technique_resp = MagicMock()
        technique_resp.parsed = {"technique": "triframe", "reasoning": "complex motion"}
        technique_resp.provider = "planner"
        technique_resp.model = "planner-model"
        technique_resp.request_id = "p-0"
        technique_resp.usage = {"tokens": 5}

        triframe_resp = MagicMock()
        triframe_resp.parsed = {
            "base_frame": BASE_FRAME,
            "second_frame_change": SECOND_FRAME_CHANGE,
            "third_frame_change": THIRD_FRAME_CHANGE,
        }
        triframe_resp.provider = "planner"
        triframe_resp.model = "planner-model"
        triframe_resp.request_id = "p-1"
        triframe_resp.usage = {"tokens": 10}

        text_provider.generate.side_effect = [technique_resp, triframe_resp]

        image_tools = ImageTools(self.config, "tri_frame", self.manager)
        animation_tools = AnimationTools(image_tools, image_provider, text_provider, MagicMock())

        result = animation_tools.create_animation("A hero running across a bridge.", "hero_run")
        animation_tools.join_generation()

        self.assertIn("started", result)
        animation_id = re.search(r"Animation ID: '([^']+)'", result).group(1)
        self.assertEqual(image_provider.generate.call_count, 3)
        self.assertEqual(text_provider.generate.call_count, 2)
        self.assertTrue(os.path.exists(image_tools.image_aliases[f"{animation_id}_frame_1"]))
        self.assertTrue(os.path.exists(image_tools.image_aliases[f"{animation_id}_frame_2"]))
        self.assertTrue(os.path.exists(image_tools.image_aliases[f"{animation_id}_frame_3"]))

    @patch("tools.image_tool.get_image_provider")
    def test_play_animation_publishes_saved_triframe_to_canvas_state(self, mock_get_provider):
        image_provider = MagicMock()
        image_provider.generate.return_value = ImageGenerationResult(
            image_bytes=fake_image_bytes(), mime_type="image/jpeg", provider="fake", model="fake-model"
        )
        text_provider = MagicMock()
        technique_resp = MagicMock(parsed={"technique": "triframe", "reasoning": "motion"}, provider="p", model="m", request_id="1", usage={})
        triframe_resp = MagicMock(
            parsed={
                "base_frame": "A candle burns in a still room.",
                "second_frame_change": "The flame leans right.",
                "third_frame_change": "The flame returns upright.",
            },
            provider="p", model="m", request_id="2", usage={}
        )
        text_provider.generate.side_effect = [technique_resp, triframe_resp]

        canvas_state_service = CanvasStateService(self.manager)
        image_tools = ImageTools(
            self.config, "tri_frame_canvas", self.manager, canvas_state_service=canvas_state_service
        )
        animation_tools = AnimationTools(image_tools, image_provider, text_provider, MagicMock())

        result = animation_tools.create_animation("A candle burns in a still room.", "candle")
        animation_tools.join_generation()
        animation_id = re.search(r"Animation ID: '([^']+)'", result).group(1)

        self.assertNotIn("animation", canvas_state_service.latest_state("tri_frame_canvas"))
        self.assertIn("Playing animation", animation_tools.play_animation(animation_id))

        animation = canvas_state_service.latest_state("tri_frame_canvas")["animation"]
        self.assertEqual(animation["type"], "triframe")
        self.assertEqual(len(animation["frames"]), 3)
        self.assertIn(f"/output/animations/{animation_id}/frame_1.jpg", animation["frames"][0])

    @patch("tools.image_tool.get_image_provider")
    def test_create_animation_passes_references_to_each_frame(self, mock_get_provider):
        image_provider = MagicMock()
        image_provider.generate.return_value = ImageGenerationResult(
            image_bytes=fake_image_bytes(), mime_type="image/jpeg", provider="fake", model="fake-model"
        )
        text_provider = MagicMock()
        technique_resp = MagicMock(parsed={"technique": "triframe", "reasoning": "motion"}, provider="p", model="m", request_id="1", usage={})
        triframe_resp = MagicMock(
            parsed={
                "base_frame": "A hero stands in a courtyard.",
                "second_frame_change": "The hero lifts a hand.",
                "third_frame_change": "The hero lowers the hand.",
            },
            provider="p", model="m", request_id="2", usage={}
        )
        text_provider.generate.side_effect = [technique_resp, triframe_resp]

        image_tools = ImageTools(self.config, "tri_frame_references", self.manager)
        reference_path = os.path.join(image_tools.reference_dir, "hero.png")
        Image.new("RGB", (10, 10), color="red").save(reference_path)
        image_tools._load_references()
        animation_tools = AnimationTools(image_tools, image_provider, text_provider, MagicMock())

        animation_tools.create_animation("A hero stands in a courtyard.", "hero_stand", reference_images="hero")
        animation_tools.join_generation()

        requests = [call.args[0] for call in image_provider.generate.call_args_list]
        self.assertEqual(requests[0].references[0].name, "hero.png")
        self.assertEqual(len(requests[1].references), 2)

    @patch("tools.image_tool.get_image_provider")
    def test_create_animation_rejects_unknown_reference(self, mock_get_provider):
        image_tools = ImageTools(self.config, "tri_frame_missing_reference", self.manager)
        animation_tools = AnimationTools(image_tools, MagicMock(), MagicMock(), MagicMock())

        result = animation_tools.create_animation("A hero stands in a courtyard.", "hero_stand", reference_images="missing")

        self.assertIn("Reference image 'missing' not found", result)

    @patch("tools.image_tool.get_image_provider")
    def test_create_animation_requires_animation_name(self, mock_get_provider):
        image_tools = ImageTools(self.config, "missing_anim_name", self.manager)
        animation_tools = AnimationTools(image_tools, MagicMock(), MagicMock(), MagicMock())

        result = animation_tools.create_animation("A hero stands in a courtyard.", "")
        self.assertIn("animation_name is required", result)

    @patch("tools.image_tool.get_image_provider")
    def test_animation_cooldown_uses_animation_configuration(self, mock_get_provider):
        image_tools = ImageTools(self.config, "tri_frame_cooldown", self.manager)
        animation_tools = AnimationTools(
            image_tools, MagicMock(), MagicMock(), MagicMock(), {"enabled": True, "cooldown_duration": 27}
        )

        self.assertEqual(animation_tools.cooldown_duration, 27)

    @patch("tools.image_tool.get_image_provider")
    def test_create_animation_uses_layered_technique(self, mock_get_provider):
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
        technique_resp = MagicMock(parsed={"technique": "layered", "reasoning": "scenic backdrop"}, provider="p", model="m", request_id="1", usage={})
        layered_resp = MagicMock(
            parsed={
                "background": {"description": "distant sky and cliff", "effect": "none"},
                "subject": {"description": "the hero on the cliff", "effect": "pulse"},
                "foreground": {"description": "foreground leaves", "effect": "sway"},
            },
            provider="planner", model="planner-model", request_id="planner-1", usage={"tokens": 42}
        )
        planning_provider.generate.side_effect = [technique_resp, layered_resp]

        canvas_state_service = CanvasStateService(self.manager)
        image_tools = ImageTools(self.config, "layered_canvas", self.manager, canvas_state_service=canvas_state_service)
        tools = AnimationTools(image_tools, image_provider, planning_provider, layered_provider, {"cooldown_duration": 0})

        result = tools.create_animation("A hero on a scenic cliff with foreground leaves.", "cliff")
        tools.join_generation()
        animation_id = re.search(r"Animation ID: '([^']+)'", result).group(1)
        manifest_path = os.path.join(tools.animations_dir, animation_id, "layered.json")
        with open(manifest_path, encoding="utf-8") as stream:
            manifest = __import__("json").load(stream)
        self.assertEqual(len(manifest["layers"]), 3)
        self.assertEqual(manifest["layers"][-1]["effect"], "sway")
        self.assertFalse(os.path.isabs(manifest["base_image"]))
        self.assertFalse(os.path.isabs(manifest["layers"][0]["path"]))

        self.assertIn("Playing layered animation", tools.play_animation(animation_id))
        animation = canvas_state_service.latest_state("layered_canvas")["animation"]
        self.assertEqual(animation["type"], "layered")
        self.assertEqual(animation["layers"][-1]["effect"], "sway")

    @patch("tools.image_tool.get_image_provider")
    def test_create_triframe_animation_outputs_triframe_json(self, mock_get_provider):
        image_provider = MagicMock()
        image_provider.generate.return_value = ImageGenerationResult(
            image_bytes=fake_image_bytes(), mime_type="image/jpeg", provider="fake", model="fake-model"
        )
        text_provider = MagicMock()
        technique_resp = MagicMock(parsed={"technique": "triframe", "reasoning": "complex motion"}, provider="p", model="m", request_id="1", usage={})
        triframe_resp = MagicMock(
            parsed={
                "base_frame": BASE_FRAME,
                "second_frame_change": SECOND_FRAME_CHANGE,
                "third_frame_change": THIRD_FRAME_CHANGE,
            },
            provider="p", model="m", request_id="2", usage={}
        )
        text_provider.generate.side_effect = [technique_resp, triframe_resp]

        image_tools = ImageTools(self.config, "tri_json_test", self.manager)
        animation_tools = AnimationTools(image_tools, image_provider, text_provider, MagicMock())

        result = animation_tools.create_animation("A hero running across a bridge.", "hero_run")
        animation_tools.join_generation()

        animation_id = re.search(r"Animation ID: '([^']+)'", result).group(1)
        manifest_path = os.path.join(animation_tools.animations_dir, animation_id, "triframe.json")
        self.assertTrue(os.path.exists(manifest_path))

        with open(manifest_path, encoding="utf-8") as stream:
            manifest = __import__("json").load(stream)
        self.assertEqual(manifest["type"], "triframe")
        self.assertEqual(len(manifest["frames"]), 3)
        self.assertEqual(manifest["scene_prompt"], "A hero running across a bridge.")

    @patch("tools.image_tool.get_image_provider")
    def test_browse_animations_returns_saved_animations(self, mock_get_provider):
        image_provider = MagicMock()
        image_provider.generate.return_value = ImageGenerationResult(
            image_bytes=fake_image_bytes(), mime_type="image/jpeg", provider="fake", model="fake-model"
        )
        text_provider = MagicMock()
        technique_resp = MagicMock(parsed={"technique": "triframe", "reasoning": "complex motion"}, provider="p", model="m", request_id="1", usage={})
        triframe_resp = MagicMock(
            parsed={
                "base_frame": BASE_FRAME,
                "second_frame_change": SECOND_FRAME_CHANGE,
                "third_frame_change": THIRD_FRAME_CHANGE,
            },
            provider="p", model="m", request_id="2", usage={}
        )
        text_provider.generate.side_effect = [technique_resp, triframe_resp]

        image_tools = ImageTools(self.config, "browse_test", self.manager)
        animation_tools = AnimationTools(image_tools, image_provider, text_provider, MagicMock())

        result = animation_tools.create_animation("A hero running across a bridge.", "hero_run")
        animation_tools.join_generation()

        animations = animation_tools.browse_animations()
        self.assertEqual(len(animations), 1)
        self.assertEqual(animations[0]["type"], "triframe")
        self.assertEqual(animations[0]["scene_prompt"], "A hero running across a bridge.")

    @patch("tools.image_tool.get_image_provider")
    def test_create_animation_is_single_flight_while_generating(self, mock_get_provider):
        started = threading.Event()
        release = threading.Event()
        planner = MagicMock()
        technique_resp = MagicMock(parsed={"technique": "layered", "reasoning": "backdrop"}, provider="p", model="m", request_id="1", usage={})

        def plan(_request):
            started.set()
            release.wait(timeout=2)
            return technique_resp

        planner.generate.side_effect = plan
        image_provider = MagicMock()
        layered_provider = MagicMock(model="fal-ai/qwen-image-layered")
        image_tools = ImageTools(self.config, "single_flight", self.manager)
        tools = AnimationTools(image_tools, image_provider, planner, layered_provider, {"cooldown_duration": 0})
        first_result = []
        first = threading.Thread(target=lambda: first_result.append(tools.create_animation("A traveler under stars.", "traveler")))
        first.start()
        self.assertTrue(started.wait(timeout=1))

        second_result = tools.create_animation("A second scene.", "second_scene")
        self.assertIn("already being generated", second_result)
        release.set()
        first.join(timeout=2)
        tools.join_generation(timeout=2)
        self.assertIn("generation started", first_result[0])
        self.assertFalse(tools.is_in_flight("create_animation"))

    def test_animation_layer_accepts_twist_and_bend_effects(self):
        from tools.animation_tool import AnimationLayer
        layer_twist = AnimationLayer(description="a spinning magic portal", effect="twist")
        self.assertEqual(layer_twist.effect, "twist")
        layer_bend = AnimationLayer(description="a flexing reed", effect="bend")
        self.assertEqual(layer_bend.effect, "bend")

    def test_plan_animation_technique_with_provider_guidance(self):
        text_provider = MagicMock()
        text_provider.generate.return_value.parsed = {
            "technique": "triframe",
            "reasoning": "complex character walk sequence",
        }
        text_provider.generate.return_value.provider = "test-provider"
        text_provider.generate.return_value.model = "test-model"
        text_provider.generate.return_value.request_id = "req-1"
        text_provider.generate.return_value.usage = {"prompt_tokens": 15}

        technique, debug = AnimationTools.plan_animation_technique_with_provider(
            text_provider, "A wizard walking and casting a spell."
        )

        self.assertEqual(technique, "triframe")
        self.assertEqual(debug["reasoning"], "complex character walk sequence")
        request = text_provider.generate.call_args.args[0]
        self.assertIn("complex motions and transitions", request.prompt)
        self.assertIn("scenic backdrops", request.prompt)
        self.assertIn("high energy single moment climaxes", request.prompt)

    def test_decomposition_prompt_format(self):
        plan = [
            {"name": "background", "description": "distant sky"},
            {"name": "subject", "description": "hero on cliff"},
            {"name": "foreground", "description": "swaying leaves"},
        ]
        prompt = AnimationTools._decomposition_prompt("A hero on a cliff.", plan)
        self.assertEqual(prompt, "background: distant sky; subject: hero on cliff; foreground: swaying leaves")
        self.assertNotIn("RGBA", prompt)
        self.assertNotIn("ordered", prompt)
        self.assertNotIn("back to front", prompt)

    @patch("tools.image_tool.get_image_provider")
    def test_on_animation_ready_notifies_callback_for_triframe(self, mock_get_provider):
        image_provider = MagicMock()
        image_provider.generate.return_value = ImageGenerationResult(
            image_bytes=fake_image_bytes(), mime_type="image/jpeg", provider="fake", model="fake-model"
        )
        text_provider = MagicMock()
        technique_resp = MagicMock(parsed={"technique": "triframe", "reasoning": "motion"}, provider="p", model="m", request_id="1", usage={})
        triframe_resp = MagicMock(
            parsed={
                "base_frame": BASE_FRAME,
                "second_frame_change": SECOND_FRAME_CHANGE,
                "third_frame_change": THIRD_FRAME_CHANGE,
            },
            provider="p", model="m", request_id="2", usage={}
        )
        text_provider.generate.side_effect = [technique_resp, triframe_resp]

        image_tools = ImageTools(self.config, "tri_callback", self.manager)
        animation_tools = AnimationTools(image_tools, image_provider, text_provider, MagicMock())

        ready_notifications = []
        animation_tools.on_animation_ready = lambda anim_id, technique: ready_notifications.append((anim_id, technique))

        result = animation_tools.create_animation("A hero running across a bridge.", "hero_run")
        animation_tools.join_generation()

        animation_id = re.search(r"Animation ID: '([^']+)'", result).group(1)
        self.assertEqual(len(ready_notifications), 1)
        self.assertEqual(ready_notifications[0], (animation_id, "triframe"))

    @patch("tools.image_tool.get_image_provider")
    def test_on_animation_ready_notifies_callback_for_layered(self, mock_get_provider):
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
        technique_resp = MagicMock(parsed={"technique": "layered", "reasoning": "scenic backdrop"}, provider="p", model="m", request_id="1", usage={})
        layered_resp = MagicMock(
            parsed={
                "background": {"description": "distant sky and cliff", "effect": "none"},
                "subject": {"description": "the hero on the cliff", "effect": "pulse"},
                "foreground": {"description": "foreground leaves", "effect": "sway"},
            },
            provider="planner", model="planner-model", request_id="planner-1", usage={"tokens": 42}
        )
        planning_provider.generate.side_effect = [technique_resp, layered_resp]

        image_tools = ImageTools(self.config, "layered_callback", self.manager)
        tools = AnimationTools(image_tools, image_provider, planning_provider, layered_provider, {"cooldown_duration": 0})

        ready_notifications = []
        tools.on_animation_ready = lambda anim_id, technique: ready_notifications.append((anim_id, technique))

        result = tools.create_animation("A hero on a scenic cliff.", "cliff")
        tools.join_generation()

        animation_id = re.search(r"Animation ID: '([^']+)'", result).group(1)
        self.assertEqual(len(ready_notifications), 1)
        self.assertEqual(ready_notifications[0], (animation_id, "layered"))

