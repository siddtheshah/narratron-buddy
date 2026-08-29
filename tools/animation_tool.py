"""Tools for generating small, loop-ready image animation sequences."""

from __future__ import annotations

import logging
import json
import os
import re
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Optional, Union

from PIL import Image
from pydantic import BaseModel, Field

from providers import (
    ImageGenerationRequest,
    ImageProvider,
    ImageProviderError,
    ImageReference,
    TextResponseProvider,
    TextResponseProviderError,
    TextResponseRequest,
)
from providers.fal_qwen_layered_provider import FalQwenLayeredProvider, LayeredImageRequest
from tools.base_tool import BaseTools, single_flight, with_cooldown
from utils.image_utils import embed_image_metadata


logger = logging.getLogger(__name__)


class AnimationLayer(BaseModel):
    description: str = Field(min_length=3, max_length=300)
    effect: str = Field(pattern="^(none|sway|vibrate|pulse|twist|bend|gentle_rocking)$")


class AnimationLayerPlanResponse(BaseModel):
    background: AnimationLayer
    subject: AnimationLayer
    foreground: AnimationLayer | None = None


ANIMATION_LAYER_PLAN_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "background": {"$ref": "#/$defs/layer"},
        "subject": {"$ref": "#/$defs/layer"},
        "foreground": {"anyOf": [{"$ref": "#/$defs/layer"}, {"type": "null"}]},
    },
    "$defs": {
        "layer": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "minLength": 3, "maxLength": 180},
                "effect": {"type": "string", "enum": ["none", "sway", "vibrate", "pulse", "twist", "bend", "gentle_rocking"]},
            },
            "required": ["description", "effect"],
            "additionalProperties": False,
        },
    },
    "required": ["background", "subject"],
    "additionalProperties": False,
}


class TriframePlanResponse(BaseModel):
    base_frame: str = Field(min_length=3, max_length=500)
    second_frame_change: str = Field(min_length=3, max_length=300)
    third_frame_change: str = Field(min_length=3, max_length=300)


TRIFRAME_PLAN_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "base_frame": {"type": "string", "minLength": 3, "maxLength": 500},
        "second_frame_change": {"type": "string", "minLength": 3, "maxLength": 300},
        "third_frame_change": {"type": "string", "minLength": 3, "maxLength": 300},
    },
    "required": ["base_frame", "second_frame_change", "third_frame_change"],
    "additionalProperties": False,
}


class AnimationTechniquePlanResponse(BaseModel):
    technique: str = Field(pattern="^(triframe|layered)$")
    reasoning: str = Field(min_length=3, max_length=300)


ANIMATION_TECHNIQUE_PLAN_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "technique": {"type": "string", "enum": ["triframe", "layered"]},
        "reasoning": {"type": "string", "minLength": 3, "maxLength": 300},
    },
    "required": ["technique", "reasoning"],
    "additionalProperties": False,
}




class AnimationTools(BaseTools):
    """Create image sequences using the same provider as regular canvas images."""

    def __init__(
        self,
        image_tools,
        image_provider: ImageProvider,
        text_response_provider: TextResponseProvider,
        layered_provider: FalQwenLayeredProvider,
        animation_config: Optional[dict] = None,
    ):
        # ImageTools owns the theater-specific output directory, aliases, canvas
        # hooks, and configured image-generation cooldown.
        super().__init__(
            config=animation_config or {},
            theater_id=image_tools.active_theater_id,
            canvas_state_service=image_tools.canvas_state_service,
            default_cooldown=image_tools.cooldown_duration,
        )
        self.image_tools = image_tools
        self.image_provider = image_provider
        self.output_dir = image_tools.output_dir
        self.animations_dir = os.path.join(str(image_tools.theater.output_dir()), "animations")
        os.makedirs(self.animations_dir, exist_ok=True)
        self.default_style = image_tools.default_style
        self._animations: dict[str, list[str]] = {}
        self._layered_animations: dict[str, dict] = {}
        self.layered_provider = layered_provider
        self.text_response_provider = text_response_provider

    def join_generation(self, timeout: float = 30.0) -> None:
        """Wait for the latest animation generation; useful in tests and teardown."""
        thread = getattr(self, "_last_generation_thread", None)
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    @single_flight(
        error_message="An animation is already being generated. Please wait for it to complete.",
        hold_until_released=True,
        timeout=80.0,
    )
    @with_cooldown("generating another animation")
    def create_animation(
        self,
        scene_prompt: str,
        animation_name: str,
        reference_images: Union[list[str], str, None] = None,
    ) -> str:
        """Generate an animation sequence by automatically deciding between triframe and layered techniques.

        The caller supplies a scene prompt and animation name. An internal LLM decision prompt determines whether to use:
        - 'triframe' for complex motions and transitions
        - 'layered' for scenic backdrops or high-energy single-moment climaxes

        Args:
            scene_prompt: Detailed prompt describing the scene to animate.
            animation_name: Friendly name used for saved animation files.
            reference_images: Optional image aliases or paths to preserve.

        Returns:
            A status message immediately; animation generation continues in background.
        """
        if not isinstance(scene_prompt, str) or not scene_prompt.strip():
            return "Error: scene_prompt is required."
        if not isinstance(animation_name, str) or not animation_name.strip():
            return "Error: animation_name is required."

        provider_references, reference_error = self._resolve_provider_references(reference_images)
        if reference_error:
            return reference_error

        self.record_tool_call("create_animation")
        timestamp = int(time.time())
        clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", animation_name.strip())
        animation_id = f"{clean_name}_{timestamp}"

        def _worker() -> None:
            try:
                technique, decision_debug = self.plan_animation_technique_with_provider(
                    self.text_response_provider, scene_prompt.strip()
                )
                logger.debug(
                    "[AnimationTools] Animation %s decided technique '%s', debug=%s",
                    animation_id,
                    technique,
                    decision_debug,
                )
                if technique == "triframe":
                    self._run_triframe_animation(scene_prompt.strip(), animation_id, provider_references)
                else:
                    self._run_layered_animation(scene_prompt.strip(), animation_id)
            except Exception:
                logger.exception("[AnimationTools] Failed to create animation for %s", animation_id)
            finally:
                self.release_in_flight("create_animation")

        thread = threading.Thread(target=_worker, daemon=True)
        self._last_generation_thread = thread
        thread.start()
        return (
            f"Animation generation started in background with name '{animation_name.strip()}'. "
            f"Animation ID: '{animation_id}'. Call play_animation with that ID once it is ready."
        )

    def _run_triframe_animation(
        self,
        scene_prompt: str,
        animation_id: str,
        provider_references: list[ImageReference],
    ) -> None:
        """Run the triframe generation pipeline."""
        self.image_tools._set_canvas_activity(True)
        try:
            plan, planner_debug = self.plan_triframe_with_provider(
                self.text_response_provider, scene_prompt
            )
            logger.debug("[AnimationTools] Triframe animation %s LLM plan=%s", animation_id, plan)
            effective_base_frame = self.image_tools._apply_default_style(plan["base_frame"].strip())

            animation_dir = os.path.join(self.animations_dir, animation_id)
            os.makedirs(animation_dir, exist_ok=False)
            previous_frame: Optional[ImageReference] = None
            saved_paths = []
            frame_prompts = [
                effective_base_frame,
                f"{effective_base_frame}\n\nApply this specific change to the supplied previous image: {plan['second_frame_change'].strip()}",
                f"{effective_base_frame}\n\nApply this specific change to the supplied previous image: {plan['third_frame_change'].strip()}",
            ]
            for frame_number, frame_prompt in enumerate(frame_prompts, start=1):
                frame_prompt = (
                    f"{frame_prompt}\n\n"
                    "Return exactly one full-bleed scene image for this request. "
                    "Do not create a collage, triptych, storyboard, contact sheet, split panels, "
                    "borders, or multiple views in one image. Render one cinematic still image only."
                )
                result = self.image_provider.generate(
                    ImageGenerationRequest(
                        prompt=frame_prompt,
                        references=([previous_frame] if previous_frame else []) + provider_references,
                        aspect_ratio="16:9",
                    )
                )
                filepath = self._save_frame(
                    result.image_bytes,
                    frame_prompt,
                    animation_dir,
                    frame_number,
                )
                saved_paths.append(filepath)
                previous_frame = ImageReference(
                    name=Path(filepath).name,
                    data=result.image_bytes,
                    mime_type=result.mime_type or "image/jpeg",
                )
                self._register_frame_aliases(animation_id, frame_number, filepath)
                self._notify_image_created(filepath)
                logger.debug(
                    "[AnimationTools] Saved frame %s using provider '%s' model '%s' to %s",
                    frame_number,
                    result.provider,
                    result.model,
                    filepath,
                )
            self._animations[animation_id] = saved_paths
            logger.debug("[AnimationTools] Animation '%s' is ready to play.", animation_id)
        except (ImageProviderError, TextResponseProviderError) as exc:
            logger.error("[AnimationTools] Image or text provider failed: %s", exc)
        except Exception:
            logger.exception("[AnimationTools] Failed to generate tri-frame animation")
        finally:
            self.image_tools._set_canvas_activity(False)
            self.image_tools._trigger_after_tool_call("create_animation")

    def _run_layered_animation(self, scene_prompt: str, animation_id: str) -> None:
        """Run the long-lived pipeline after its public single-flight lease is acquired."""
        self.image_tools._set_canvas_activity(True)
        try:
            animation_dir = Path(self.animations_dir) / animation_id
            animation_dir.mkdir(parents=True, exist_ok=False)
            plan, planner_debug = self.plan_layers_with_provider(self.text_response_provider, scene_prompt.strip())
            logger.debug("[AnimationTools] Layered animation %s LLM plan=%s", animation_id, plan)
            flattened_prompt = self._flatten_layer_plan(scene_prompt.strip(), plan)
            base_result = self.image_provider.generate(ImageGenerationRequest(prompt=self.image_tools._apply_default_style(flattened_prompt), aspect_ratio="16:9"))
            base_path = self._save_named_image(base_result.image_bytes, animation_dir / "base.jpg", flattened_prompt)
            decomposition_prompt = self._decomposition_prompt(scene_prompt.strip(), plan)
            provider = self.layered_provider
            layered = provider.decompose(LayeredImageRequest(image_bytes=base_result.image_bytes, mime_type=base_result.mime_type or "image/jpeg", prompt=decomposition_prompt, num_layers=len(plan)))
            layer_paths = [self._save_named_image(image_bytes, animation_dir / f"layer_{index + 1}.png", decomposition_prompt) for index, (image_bytes, _mime_type) in enumerate(layered.images[:len(plan)])]
            if len(layer_paths) < 2:
                raise ImageProviderError("Layer decomposition did not produce enough playable layers.")
            manifest = {"version": 1, "id": animation_id, "scene_prompt": scene_prompt.strip(), "flattened_prompt": flattened_prompt, "decomposition_prompt": decomposition_prompt, "base_image": str(base_path), "layers": [{**description, "path": str(path), "order": index} for index, (description, path) in enumerate(zip(plan[:len(layer_paths)], layer_paths))], "provider": {"base": base_result.provider, "base_model": base_result.model, "decomposition": provider.model, "request_id": layered.request_id, "usage": layered.usage, "planner": planner_debug}}
            manifest_path = animation_dir / "animation.json"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            self._layered_animations[animation_id] = manifest
            self._register_layered_aliases(animation_id, base_path, layer_paths)
            self._notify_image_created(str(base_path))
            if self.canvas_state_service:
                self.canvas_state_service.show_layered_animation(manifest, theater_id=self.active_theater_id)
            logger.debug("[AnimationTools] Layered animation ready id=%s base=%s layers=%s manifest=%s", animation_id, base_path, len(layer_paths), manifest_path)
        except ImageProviderError as exc:
            logger.error("[AnimationTools] Layered image provider failed for %s: %s", animation_id, exc)
        except Exception:
            logger.exception("[AnimationTools] Layered animation failed for %s", animation_id)
        finally:
            self.image_tools._set_canvas_activity(False)
            self.image_tools._trigger_after_tool_call("create_animation")

    @staticmethod
    def plan_animation_technique_with_provider(
        text_response_provider: TextResponseProvider,
        scene_prompt: str,
    ) -> tuple[str, dict[str, object]]:
        """Use the text provider to decide whether to generate a triframe or layered animation."""
        request = TextResponseRequest(
            prompt=(
                "Decide whether to use 'triframe' or 'layered' animation technique for a given 2D scene.\n"
                "Return JSON with two keys:\n"
                "1. technique: Must be either 'triframe' or 'layered'.\n"
                "2. reasoning: Short explanation of why this technique was chosen.\n\n"
                "GUIDANCE ON TECHNIQUE CHOICE:\n"
                "- For a scene that involves complex motions and transitions, use 'triframe'.\n"
                "- Avoid using 'triframe' (use 'layered' instead) for scenes that are scenic backdrops, or during high energy single moment climaxes.\n\n"
                f"Scene prompt:\n{scene_prompt}"
            ),
            temperature=0.1,
            max_output_tokens=256,
            response_json_schema=ANIMATION_TECHNIQUE_PLAN_JSON_SCHEMA,
        )
        try:
            response = text_response_provider.generate(request)
        except TextResponseProviderError as exc:
            logger.warning("[AnimationTools] Technique planner returned invalid output; retrying once: %s", exc)
            response = text_response_provider.generate(request)
        try:
            parsed = response.parsed
            draft = (
                parsed
                if isinstance(parsed, AnimationTechniquePlanResponse)
                else AnimationTechniquePlanResponse.model_validate(parsed)
            )
            return draft.technique, {
                "provider": response.provider,
                "model": response.model,
                "request_id": response.request_id,
                "usage": dict(response.usage),
                "reasoning": draft.reasoning,
            }
        except Exception as exc:
            logger.warning("[AnimationTools] Technique planner parsing failed, defaulting to layered: %s", exc)
            return "layered", {"error": str(exc)}


    @staticmethod
    def plan_triframe_with_provider(
        text_response_provider: TextResponseProvider,
        scene_prompt: str,
    ) -> tuple[dict[str, str], dict[str, object]]:
        """Use the text provider to plan base frame and key changes for a 3-frame animation sequence."""
        request = TextResponseRequest(
            prompt=(
                "Plan a 3-frame looping animation sequence for a 2D scene.\n"
                "Return JSON with three keys:\n"
                "1. base_frame: A complete, highly descriptive prompt for the initial full-bleed scene image.\n"
                "2. second_frame_change: Specific, clear action or motion change applied to the initial image for the second frame.\n"
                "3. third_frame_change: Specific, clear action or motion change applied to the second image for the third frame, leading back towards the initial state to create a smooth loop.\n\n"
                "IMPORTANT: Make sure there is a clear action difference between frames. "
                "For example, progressive positional movement like 'walking', 'further along', and 'even further' is BAD. "
                "Use distinct key actions like 'walking', 'further and looking back', 'walks and waves back'.\n\n"
                f"Scene prompt:\n{scene_prompt}"
            ),
            temperature=0.1,
            max_output_tokens=512,
            response_json_schema=TRIFRAME_PLAN_JSON_SCHEMA,
        )
        try:
            response = text_response_provider.generate(request)
        except TextResponseProviderError as exc:
            logger.warning("[AnimationTools] Triframe planner returned invalid structured output; retrying once: %s", exc)
            response = text_response_provider.generate(request)
        parsed = response.parsed
        draft = parsed if isinstance(parsed, TriframePlanResponse) else TriframePlanResponse.model_validate(parsed)
        plan = {
            "base_frame": draft.base_frame,
            "second_frame_change": draft.second_frame_change,
            "third_frame_change": draft.third_frame_change,
        }
        return plan, {
            "provider": response.provider,
            "model": response.model,
            "request_id": response.request_id,
            "usage": dict(response.usage),
        }

    @staticmethod
    def plan_layers_with_provider(
        text_response_provider: TextResponseProvider,
        scene_prompt: str,
    ) -> tuple[list[dict[str, str]], dict[str, object]]:
        """Use the text provider to return the exact layer grounding for Qwen.

        There is intentionally no heuristic or recovery plan: a valid LLM plan
        is a prerequisite to creating a layered animation.
        """
        request = TextResponseRequest(
            prompt=("Plan a clean, semantic transparent-layer stack for a locally animated 2D scene. Return exactly one required background, one required subject, "
                    "and an optional foreground (null when there is no near-camera occluder). Descriptions must be concise (14 words or fewer). "
                    "BACKGROUND: combine the entire static environment behind the focal subject into one layer—never split it into sky, coast, sea, terrain, buildings, or lighting layers. Its effect must be none. "
                    "SUBJECT: the single focal character, creature, vehicle, or landmark, including attached parts and immediately associated light. Do not make a second subject layer. "
                    "FOREGROUND: only close-to-camera objects that visibly overlap or frame the subject, such as leaves, grass, smoke, rain, or nearby waves; otherwise use null. "
                    "Use effect=none for fixed content; sway for foliage; gentle_rocking for airborne objects; vibrate for rumbling objects; pulse for important objects; bend if the subject layer features heroic dynamic action by a character; twist for rotating or swirling cyclical motion around centroid. "
                    "Do not invent scene elements. Return JSON only.\n\n"
                    f"Scene prompt:\n{scene_prompt}"),
            temperature=0.1, max_output_tokens=512,
            response_json_schema=ANIMATION_LAYER_PLAN_JSON_SCHEMA,
        )
        # A truncated structured response is an upstream-model failure, not a
        # reason to manufacture a plan. Retry once with the same constrained
        # LLM request; a second failure is surfaced to the caller.
        try:
            response = text_response_provider.generate(request)
        except TextResponseProviderError as exc:
            logger.warning("[AnimationTools] Layer planner returned invalid structured output; retrying once: %s", exc)
            response = text_response_provider.generate(request)
        parsed = response.parsed
        draft = parsed if isinstance(parsed, AnimationLayerPlanResponse) else AnimationLayerPlanResponse.model_validate(parsed)
        plan = [
            {"name": "background", **draft.background.model_dump()},
            {"name": "subject", **draft.subject.model_dump()},
        ]
        if draft.foreground is not None:
            plan.append({"name": "foreground", **draft.foreground.model_dump()})
        return plan, {"provider": response.provider, "model": response.model, "request_id": response.request_id, "usage": dict(response.usage)}

    @staticmethod
    def _flatten_layer_plan(scene_prompt: str, plan: list[dict[str, str]]) -> str:
        descriptions = "; ".join(f"{item['name']}: {item['description']}" for item in plan)
        return f"{scene_prompt}\n\nCompose a single full-bleed 16:9 cinematic still with clear depth separation. Planned depth regions: {descriptions}."

    @staticmethod
    def _decomposition_prompt(scene_prompt: str, plan: list[dict[str, str]]) -> str:
        regions = "; ".join(f"layer {index + 1} ({item['name']}): {item['description']}" for index, item in enumerate(plan))
        return f"Decompose this exact scene into {len(plan)} separate transparent RGBA layers, ordered back to front. Scene: {scene_prompt}. Ground the decomposition in these intended regions: {regions}. Preserve all visible scene content across the layers; no borders, text, or collage."

    @staticmethod
    def _save_named_image(image_bytes: bytes, path: Path, prompt: str) -> str:
        image = Image.open(BytesIO(image_bytes))
        if path.suffix.lower() == ".jpg" and image.mode != "RGB":
            image = image.convert("RGB")
        exif = image.getexif()
        embed_image_metadata(exif, prompt)
        image.save(path, "PNG" if path.suffix.lower() == ".png" else "JPEG", exif=exif, quality=95)
        return str(path)

    def _register_layered_aliases(self, animation_id: str, base_path: str, layer_paths: list[str]) -> None:
        self.image_tools.image_aliases[f"{animation_id}_base"] = base_path
        for index, path in enumerate(layer_paths, start=1):
            self.image_tools.image_aliases[f"{animation_id}_layer_{index}"] = path

    def _find_layered_animation(self, animation_id: str) -> Optional[dict]:
        if animation_id in self._layered_animations:
            return self._layered_animations[animation_id]
        clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", animation_id)
        manifest_path = Path(self.animations_dir) / clean_id / "animation.json"
        if not manifest_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest.get("layers"), list) or len(manifest["layers"]) < 2:
                return None
            self._layered_animations[clean_id] = manifest
            return manifest
        except (OSError, json.JSONDecodeError):
            logger.warning("[AnimationTools] Invalid layered animation manifest: %s", manifest_path)
            return None

    def _resolve_provider_references(
        self, reference_images: Union[list[str], str, None]
    ) -> tuple[list[ImageReference], Optional[str]]:
        if not reference_images:
            return [], None
        reference_names = (
            [item.strip() for item in reference_images.split(",") if item.strip()]
            if isinstance(reference_images, str)
            else reference_images
        )
        resolved_references = []
        for reference_name in reference_names:
            reference_path = self.image_tools._find_image_path(reference_name)
            if not reference_path:
                return [], f"Error: Reference image '{reference_name}' not found."
            try:
                data = Path(reference_path).read_bytes()
            except OSError as exc:
                return [], f"Error loading reference image '{reference_name}': {exc}"
            suffix = Path(reference_path).suffix.lower()
            mime_type = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
            resolved_references.append(
                ImageReference(name=Path(reference_path).name, data=data, mime_type=mime_type)
            )
        return resolved_references, None

    @with_cooldown("playing another animation")
    def play_animation(self, animation_id: str) -> str:
        """Display a saved animation (triframe or layered) on the canvas.

        Args:
            animation_id: The ID returned by create_animation.
        """
        if not self.canvas_state_service:
            return "Error: Canvas state service is unavailable."

        manifest = self._find_layered_animation(animation_id)
        if manifest:
            self.canvas_state_service.show_layered_animation(manifest, theater_id=self.active_theater_id)
            self.image_tools._trigger_after_tool_call("play_animation")
            return f"Playing layered animation '{animation_id}'."

        frame_paths = self._find_animation(animation_id)
        if frame_paths:
            self.canvas_state_service.show_triframe(frame_paths, theater_id=self.active_theater_id)
            self.image_tools._trigger_after_tool_call("play_animation")
            return f"Playing animation '{animation_id}'."

        return f"Error: Animation '{animation_id}' was not found."


    def _save_frame(
        self,
        image_bytes: bytes,
        prompt: str,
        animation_dir: str,
        frame_number: int,
    ) -> str:
        if not image_bytes:
            raise ImageProviderError("Provider returned no binary image data.")
        image = Image.open(BytesIO(image_bytes))
        if image.mode != "RGB":
            image = image.convert("RGB")
        filename = f"frame_{frame_number}.jpg"
        filepath = os.path.join(animation_dir, filename)
        exif = image.getexif()
        embed_image_metadata(exif, prompt)
        image.save(filepath, "JPEG", exif=exif, quality=95)
        return filepath

    def _register_frame_aliases(
        self,
        animation_id: str,
        frame_number: int,
        filepath: str,
    ) -> None:
        alias = f"{animation_id}_frame_{frame_number}"
        self.image_tools.image_aliases[alias] = filepath
        self.image_tools.image_aliases[alias.lower()] = filepath

    def _find_animation(self, animation_id: str) -> list[str]:
        if animation_id in self._animations:
            return self._animations[animation_id]
        clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", animation_id)
        animation_dir = Path(self.animations_dir) / clean_id
        frame_paths = [str(animation_dir / f"frame_{number}.jpg") for number in range(1, 4)]
        if all(Path(path).is_file() for path in frame_paths):
            self._animations[clean_id] = frame_paths
            return frame_paths
        return []

    def _notify_image_created(self, filepath: str) -> None:
        callback = self.image_tools.on_image_created
        if callback:
            try:
                callback(filepath)
            except TypeError:
                try:
                    callback()
                except Exception:
                    logger.exception("[AnimationTools] Image-created callback failed")
            except Exception:
                logger.exception("[AnimationTools] Image-created callback failed")
