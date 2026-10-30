"""Synchronous animation generation shared by live tools and theater assets.

Callers own threading, canvas state, reference resolution, and billing.
Generation failures propagate to the caller.
"""

from __future__ import annotations
import json
import logging
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Literal
from PIL import Image
from pydantic import BaseModel, Field, JsonValue, TypeAdapter
from providers import (
    ImageGenerationRequest,
    ImageProvider,
    ImageProviderError,
    ImageReference,
    TextResponseProvider,
    TextResponseProviderError,
    TextResponseRequest,
    VideoGenerationRequest,
    VideoProvider,
)
from providers.registry import (
    get_image_provider,
    get_text_response_provider,
    get_video_provider,
)
from providers.fal_qwen_layered_provider import (
    FalQwenLayeredProvider,
    LayeredImageRequest,
)
from utils.image_utils import embed_image_metadata

logger = logging.getLogger(__name__)
AnimationTechnique = Literal["triframe", "layered", "video"]


class AnimationLayer(BaseModel):
    description: str = Field(min_length=3, max_length=300)
    effect: str = Field(
        pattern="^(none|sway|vibrate|pulse|twist|bend|gentle_rocking|light_halo|dark_halo|ghostly|reflective|mirage|energy_blast)$"
    )


class AnimationLayerPlanResponse(BaseModel):
    background: AnimationLayer
    subject: AnimationLayer
    foreground: AnimationLayer | None = None


ANIMATION_EFFECTS_DOCUMENTATION = (
    "AVAILABLE LAYER ANIMATION EFFECTS:\n"
    "- none: Fixed static content (always use for background layers).\n"
    "- sway: Bottom-anchored bending curvature. Foliage, trees, plants, and vegetation.\n"
    "- gentle_rocking: Gentle rotational oscillation wave for airborne objects, floating boats, or swinging lanterns.\n"
    "- vibrate: Rapid high-frequency jitter motion. Rumbling machinery or trembling earth, frightful creatures.\n"
    "- pulse: Subtle expanding and contracting scale pulse for heartbeats, breathing, or power cores.\n"
    "- twist: Cyclical motion. Strange and alien creatures.\n"
    "- bend: Dynamic character movements. People or creatures doing things.\n"
    "- light_halo: Pulsing light. Radiant power and powerful vibes\n"
    "- dark_halo: Pulsing dark. Dark power and evil vibes.\n"
    "- ghostly: Ethereal fading in and out. Spirits, phantoms, mist, vanishing memories.\n"
    "- reflective: Sweeping light trace sheen. Metallic, glass, gemstone, or polished surfaces.\n"
    "- mirage: Multi-layer twist distortion field for fires, flames, smoky environments, dizziness, and chaotic heat waves.\n"
    "- energy_blast: Jagged traveling wave fronts and centroid ripples along bright laser blasts, plasma beams, energy bursts, or comet tails."
)


class TriframePlanResponse(BaseModel):
    base_frame: str = Field(min_length=3, max_length=500)
    second_frame_change: str = Field(min_length=3, max_length=300)
    third_frame_change: str = Field(min_length=3, max_length=300)


class AnimationTechniquePlanResponse(BaseModel):
    technique: str = Field(pattern="^(triframe|layered|video)$")
    reasoning: str = Field(min_length=3, max_length=300)


class AnimationService:
    """Plan, generate, and save animations without a live canvas or agent."""

    def __init__(
        self,
        output_dir: Path,
        config: dict[str, JsonValue],
        *,
        image_provider: ImageProvider | None = None,
        text_response_provider: TextResponseProvider | None = None,
        layered_provider: FalQwenLayeredProvider | None = None,
        video_provider: VideoProvider | None = None,
    ) -> None:
        self.output_dir = str(output_dir)
        self.animations_dir = str(output_dir / "animations")
        self.animation_config = TypeAdapter(dict[str, JsonValue]).validate_python(
            config.get("animation") or {}
        )
        self.visuals_config = TypeAdapter(dict[str, JsonValue]).validate_python(
            config.get("visuals") or {}
        )
        self.default_style = str(self.visuals_config.get("style") or "").strip()
        self._image_provider = image_provider
        self._text_response_provider = text_response_provider
        self._layered_provider = layered_provider
        self._video_provider = video_provider

    @property
    def image_provider(self) -> ImageProvider:
        if self._image_provider is None:
            self._image_provider = get_image_provider(
                str(self.visuals_config.get("model") or "hybrid-flux-gemini"),
                TypeAdapter(dict[str, JsonValue]).validate_python(
                    self.visuals_config.get("model_options") or {}
                ),
            )
        return self._image_provider

    @property
    def text_response_provider(self) -> TextResponseProvider:
        if self._text_response_provider is None:
            self._text_response_provider = get_text_response_provider(
                str(self.animation_config.get("text_provider") or "gemini-3"),
                {
                    "model": str(
                        self.animation_config.get("text_model")
                        or "gemini-3.5-flash-lite"
                    )
                },
            )
        return self._text_response_provider

    @property
    def layered_provider(self) -> FalQwenLayeredProvider:
        if self._layered_provider is None:
            self._layered_provider = FalQwenLayeredProvider()
        return self._layered_provider

    @property
    def video_provider(self) -> VideoProvider:
        if self._video_provider is None:
            self._video_provider = get_video_provider(
                str(
                    self.animation_config.get("video_provider")
                    or "fal-minimax-h3-turbo"
                )
            )
        return self._video_provider

    def create_animation(
        self,
        scene_prompt: str,
        animation_id: str,
        *,
        technique: AnimationTechnique | None = None,
        references: list[ImageReference] | None = None,
    ) -> AnimationTechnique:
        """Write complete artifacts; explicit techniques override theater defaults."""
        if not scene_prompt.strip():
            raise ValueError("scene_prompt is required.")
        if not animation_id or not re.fullmatch(r"[a-zA-Z0-9_-]+", animation_id):
            raise ValueError("animation_id must be a safe directory name.")
        selected = technique or self.animation_config.get("forced_technique")
        if selected not in ("triframe", "layered", "video"):
            if technique is not None:
                raise ValueError("Unknown animation technique.")
            selected, _ = self.plan_animation_technique_with_provider(
                self.text_response_provider, scene_prompt.strip()
            )
        selected = TypeAdapter(AnimationTechnique).validate_python(selected)
        if selected == "triframe":
            self._run_triframe_animation(
                scene_prompt.strip(), animation_id, references or []
            )
        elif selected == "layered":
            self._run_layered_animation(
                scene_prompt.strip(), animation_id, references or []
            )
        else:
            self._run_video_animation(scene_prompt.strip(), animation_id)
        return selected

    def _apply_default_style(self, prompt: str) -> str:
        if self.default_style and not re.search(
            r"\bstyle\b", prompt, flags=re.IGNORECASE
        ):
            return f"{prompt}\n\nStyle: {self.default_style}"
        return prompt

    def _run_triframe_animation(
        self,
        scene_prompt: str,
        animation_id: str,
        provider_references: list[ImageReference],
    ) -> None:
        plan, planner_debug = self.plan_triframe_with_provider(
            self.text_response_provider, scene_prompt
        )
        logger.debug(
            "[AnimationService] Triframe animation %s LLM plan=%s", animation_id, plan
        )
        effective_base_frame = self._apply_default_style(plan["base_frame"].strip())
        animation_dir = os.path.join(self.animations_dir, animation_id)
        os.makedirs(animation_dir, exist_ok=False)
        previous_frame: ImageReference | None = None
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
                    references=([previous_frame] if previous_frame else [])
                    + provider_references,
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
            logger.debug(
                "[AnimationTools] Saved frame %s using provider '%s' model '%s' to %s",
                frame_number,
                result.provider,
                result.model,
                filepath,
            )
        manifest = {
            "version": 1,
            "id": animation_id,
            "type": "triframe",
            "scene_prompt": scene_prompt.strip(),
            "frames": [self._to_relative_path(p) for p in saved_paths],
            "plan": plan,
            "provider": {
                "planner": planner_debug,
            },
        }
        manifest_path = Path(animation_dir) / "triframe.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        logger.debug(
            "[AnimationService] Animation '%s' is ready to play.", animation_id
        )

    def _run_layered_animation(
        self,
        scene_prompt: str,
        animation_id: str,
        provider_references: list[ImageReference],
    ) -> None:
        animation_dir = Path(self.animations_dir) / animation_id
        animation_dir.mkdir(parents=True, exist_ok=False)
        plan, planner_debug = self.plan_layers_with_provider(
            self.text_response_provider, scene_prompt.strip()
        )
        logger.debug(
            "[AnimationService] Layered animation %s LLM plan=%s", animation_id, plan
        )
        flattened_prompt = self._flatten_layer_plan(scene_prompt.strip(), plan)
        base_result = self.image_provider.generate(
            ImageGenerationRequest(
                prompt=self._apply_default_style(flattened_prompt),
                aspect_ratio="16:9",
                references=provider_references,
            )
        )
        base_path = self._save_named_image(
            base_result.image_bytes, animation_dir / "base.webp", flattened_prompt
        )
        decomposition_prompt = self._decomposition_prompt(scene_prompt.strip(), plan)
        provider = self.layered_provider
        layered = provider.decompose(
            LayeredImageRequest(
                image_bytes=base_result.image_bytes,
                mime_type=base_result.mime_type or "image/jpeg",
                prompt=decomposition_prompt,
                num_layers=len(plan),
            )
        )
        layer_paths = [
            self._save_named_image(
                image_bytes,
                animation_dir / f"layer_{index + 1}.webp",
                decomposition_prompt,
            )
            for index, (image_bytes, _mime_type) in enumerate(
                layered.images[: len(plan)]
            )
        ]
        if len(layer_paths) != len(plan):
            raise ImageProviderError(
                "Layer decomposition did not produce enough playable layers."
            )
        manifest = {
            "version": 1,
            "type": "layered",
            "id": animation_id,
            "scene_prompt": scene_prompt.strip(),
            "flattened_prompt": flattened_prompt,
            "decomposition_prompt": decomposition_prompt,
            "base_image": self._to_relative_path(base_path),
            "layers": [
                {**description, "path": self._to_relative_path(path), "order": index}
                for index, (description, path) in enumerate(
                    zip(plan[: len(layer_paths)], layer_paths)
                )
            ],
            "provider": {
                "base": base_result.provider,
                "base_model": base_result.model,
                "decomposition": provider.model,
                "request_id": layered.request_id,
                "usage": layered.usage,
                "planner": planner_debug,
            },
        }
        manifest_path = animation_dir / "layered.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        logger.debug(
            "[AnimationService] Layered animation ready id=%s base=%s layers=%s manifest=%s",
            animation_id,
            base_path,
            len(layer_paths),
            manifest_path,
        )

    def _apply_video_style(self, prompt: str) -> str:
        """Apply the theater's image style with a loopable constraint to the video prompt."""
        prompt = prompt.strip()
        base_style = self.default_style.strip()

        if base_style:
            if re.search(r"\bloopable\b", base_style, flags=re.IGNORECASE):
                combined_style = base_style
            else:
                combined_style = f"{base_style}, loopable"
        else:
            combined_style = "loopable"

        if not re.search(r"\bstyle\b", prompt, flags=re.IGNORECASE):
            return f"{prompt}\n\nStyle: {combined_style}"

        if not re.search(r"\bloopable\b", prompt, flags=re.IGNORECASE):
            return f"{prompt}, loopable"
        return prompt

    def _run_video_animation(self, scene_prompt: str, animation_id: str) -> None:
        effective_prompt = self._apply_video_style(scene_prompt)
        animation_dir = Path(self.animations_dir) / animation_id
        animation_dir.mkdir(parents=True, exist_ok=False)
        video_duration_seconds = int(
            self.animation_config.get("video_duration_seconds") or 5
        )
        result = self.video_provider.generate(
            VideoGenerationRequest(
                prompt=effective_prompt,
                aspect_ratio="16:9",
                video_duration_seconds=video_duration_seconds,
            )
        )
        if not result.video_bytes or result.mime_type != "video/mp4":
            raise ValueError("Animation generation must return an MP4 video.")
        video_filename = "video.mp4"
        video_path = animation_dir / video_filename
        video_path.write_bytes(result.video_bytes)
        manifest = {
            "version": 1,
            "id": animation_id,
            "type": "video",
            "scene_prompt": scene_prompt.strip(),
            "effective_prompt": effective_prompt,
            "video_path": self._to_relative_path(video_path),
            "video_url": result.video_url or "",
            "mime_type": result.mime_type or "video/mp4",
            "video_duration_seconds": video_duration_seconds,
            "loop": True,
            "muted": True,
            "provider": {
                "provider": result.provider,
                "model": result.model,
                "request_id": result.request_id,
                "usage": dict(result.usage),
            },
        }
        manifest_path = animation_dir / "video.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        logger.debug(
            "[AnimationService] Video animation '%s' is ready to play at %s.",
            animation_id,
            video_path,
        )

    @staticmethod
    def plan_animation_technique_with_provider(
        text_response_provider: TextResponseProvider,
        scene_prompt: str,
    ) -> tuple[str, dict[str, JsonValue]]:
        """Use the text provider to choose triframe, layered, or video animation."""
        request = TextResponseRequest(
            prompt=(
                "Decide whether to use 'triframe', 'layered', or 'video' animation technique for a given 2D scene.\n"
                "Return JSON with two keys:\n"
                "1. technique: Must be either 'triframe', 'layered', or 'video'.\n"
                "2. reasoning: Short explanation of why this technique was chosen.\n\n"
                "GUIDANCE ON TECHNIQUE CHOICE:\n"
                "- For a scene that very high complexity motions and transitions, use 'triframe'.\n"
                "- For cinematic, continuous motion or fluid natural scenes, use 'video'.\n"
                "- Use layered for scenes that are scenic backdrops, or during high energy single moment climaxes.\n\n"
                f"Scene prompt:\n{scene_prompt}"
            ),
            temperature=0.1,
            max_output_tokens=256,
            response_schema=AnimationTechniquePlanResponse,
        )
        try:
            response = text_response_provider.generate(request)
        except TextResponseProviderError as exc:
            logger.warning(
                "[AnimationService] Technique planner returned invalid output; retrying once: %s",
                exc,
            )
            response = text_response_provider.generate(request)
        try:
            parsed = response.parsed
            draft = AnimationTechniquePlanResponse.model_validate(parsed)
            return draft.technique, {
                "provider": response.provider,
                "model": response.model,
                "request_id": response.request_id,
                "usage": dict(response.usage),
                "reasoning": draft.reasoning,
            }
        except Exception as exc:
            logger.warning(
                "[AnimationService] Technique planner parsing failed, defaulting to layered: %s",
                exc,
            )
            return "layered", {"error": str(exc)}

    @staticmethod
    def plan_triframe_with_provider(
        text_response_provider: TextResponseProvider,
        scene_prompt: str,
    ) -> tuple[dict[str, str], dict[str, JsonValue]]:
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
            response_schema=TriframePlanResponse,
        )
        try:
            response = text_response_provider.generate(request)
        except TextResponseProviderError as exc:
            logger.warning(
                "[AnimationService] Triframe planner returned invalid structured output; retrying once: %s",
                exc,
            )
            response = text_response_provider.generate(request)
        parsed = response.parsed
        draft = TriframePlanResponse.model_validate(parsed)
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
    ) -> tuple[list[dict[str, str]], dict[str, JsonValue]]:
        """Use the text provider to return the exact layer grounding for Qwen.

        There is intentionally no heuristic or recovery plan: a valid LLM plan
        is a prerequisite to creating a layered animation.
        """
        request = TextResponseRequest(
            prompt=(
                "Plan a clean, semantic transparent-layer stack for a locally animated 2D scene. Return exactly one required background, one required subject, "
                "and an optional foreground (null when there is no near-camera occluder). Descriptions must be concise (14 words or fewer).\n"
                "BACKGROUND: combine the entire static environment behind the focal subject into one layer—never split it into sky, coast, sea, terrain, buildings, or lighting layers. Its effect must be none.\n"
                "SUBJECT: the single focal character, creature, vehicle, or landmark, including attached parts and immediately associated light. Do not make a second subject layer.\n"
                "FOREGROUND: only close-to-camera objects that visibly overlap or frame the subject, such as leaves, grass, smoke, rain, or nearby waves; otherwise use null.\n\n"
                f"{ANIMATION_EFFECTS_DOCUMENTATION}\n\n"
                "Do not invent scene elements. Return JSON only.\n\n"
                f"Scene prompt:\n{scene_prompt}"
            ),
            temperature=0.1,
            max_output_tokens=512,
            response_schema=AnimationLayerPlanResponse,
        )
        # A truncated structured response is an upstream-model failure, not a
        # reason to manufacture a plan. Retry once with the same constrained
        # LLM request; a second failure is surfaced to the caller.
        try:
            response = text_response_provider.generate(request)
        except TextResponseProviderError as exc:
            logger.warning(
                "[AnimationService] Layer planner returned invalid structured output; retrying once: %s",
                exc,
            )
            response = text_response_provider.generate(request)
        parsed = response.parsed
        draft = AnimationLayerPlanResponse.model_validate(parsed)
        plan = [
            {"name": "background", **draft.background.model_dump()},
            {"name": "subject", **draft.subject.model_dump()},
        ]
        if draft.foreground is not None:
            plan.append({"name": "foreground", **draft.foreground.model_dump()})
        return plan, {
            "provider": response.provider,
            "model": response.model,
            "request_id": response.request_id,
            "usage": dict(response.usage),
        }

    @staticmethod
    def _flatten_layer_plan(scene_prompt: str, plan: list[dict[str, str]]) -> str:
        descriptions = "; ".join(
            f"{item['name']}: {item['description']}" for item in plan
        )
        return f"{scene_prompt}\n\nCompose a single full-bleed 16:9 cinematic still with clear depth separation. Planned depth regions: {descriptions}."

    @staticmethod
    def _decomposition_prompt(scene_prompt: str, plan: list[dict[str, str]]) -> str:
        return "\n".join(f"{item['name']}: {item['description']}" for item in plan)

    @staticmethod
    def _save_named_image(image_bytes: bytes, path: Path, prompt: str) -> str:
        path = path.with_suffix(".webp")
        image = Image.open(BytesIO(image_bytes))
        exif = image.getexif()
        embed_image_metadata(exif, prompt)
        image.save(path, "WEBP", exif=exif, quality=95, method=6)
        return str(path)

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
        filename = f"frame_{frame_number}.webp"
        filepath = os.path.join(animation_dir, filename)
        exif = image.getexif()
        embed_image_metadata(exif, prompt)
        image.save(filepath, "WEBP", exif=exif, quality=95, method=6)
        return filepath

    def _to_relative_path(self, path: str | Path) -> str:
        return Path(path).relative_to(self.output_dir).as_posix()
