"""Tools for generating small, loop-ready image animation sequences."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Optional, Union

from PIL import Image

from providers import ImageGenerationRequest, ImageProvider, ImageProviderError, ImageReference
from tools.base_tool import BaseTools, with_cooldown
from utils.image_utils import embed_image_metadata


logger = logging.getLogger(__name__)


class AnimationTools(BaseTools):
    """Create image sequences using the same provider as regular canvas images."""

    def __init__(
        self,
        image_tools,
        image_provider: ImageProvider,
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

    def join_generation(self, timeout: float = 30.0) -> None:
        """Wait for the latest animation generation; useful in tests and teardown."""
        thread = getattr(self, "_last_generation_thread", None)
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    @with_cooldown("generating another animation")
    def create_triframe(
        self,
        base_frame: str,
        second_frame_change: str,
        third_frame_change: str,
        animation_name: Optional[str] = None,
        reference_images: Union[list[str], str, None] = None,
    ) -> str:
        """Generate three matching images that form a short, loopable animation.

        The first and last images deliberately use nearly matching poses so a
        canvas player can cycle the files without a jarring visual jump.

        Args:
            base_frame: Complete prompt for the initial image.
            second_frame_change: Specific change to apply to the initial image for the second image.
            third_frame_change: Specific change to apply to the second image for the third image.
            animation_name: Optional friendly name used for the saved frame files.
            reference_images: Optional image aliases or paths to preserve in every frame.

        Returns:
            A status message immediately; image generation continues in the background.
        """
        if not all(isinstance(prompt, str) and prompt.strip() for prompt in (
            base_frame, second_frame_change, third_frame_change,
        )):
            return "Error: base_frame, second_frame_change, and third_frame_change are required."

        provider_references, reference_error = self._resolve_provider_references(reference_images)
        if reference_error:
            return reference_error

        effective_base_frame = self.image_tools._apply_default_style(base_frame.strip())
        self.record_tool_call("create_triframe")
        timestamp = int(time.time())
        clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", animation_name or "triframe")
        animation_id = f"{clean_name}_{timestamp}"

        def _worker() -> None:
            self.image_tools._set_canvas_activity(True)
            try:
                animation_dir = os.path.join(self.animations_dir, animation_id)
                os.makedirs(animation_dir, exist_ok=False)
                previous_frame: Optional[ImageReference] = None
                saved_paths = []
                frame_prompts = [
                    effective_base_frame,
                    f"{effective_base_frame}\n\nApply this specific change to the supplied previous image: {second_frame_change.strip()}",
                    f"{effective_base_frame}\n\nApply this specific change to the supplied previous image: {third_frame_change.strip()}",
                ]
                for frame_number, frame_prompt in enumerate(frame_prompts, start=1):
                    frame_prompt = (
                        f"{frame_prompt}\n\n"
                        "Return exactly one full-bleed scene image for this request. "
                        "Do not create a collage, triptych, storyboard, contact sheet, split panels, "
                        "borders, or multiple views in one image. Render one cinematic still image only."
                    )
                    # Each subsequent frame is image-to-image from the prior frame.
                    # This makes motion continuous instead of asking the provider to
                    # independently reproduce a scene three times.
                    result = self.image_provider.generate(
                        ImageGenerationRequest(
                            prompt=frame_prompt,
                            references=([previous_frame] if previous_frame else []) + provider_references,
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
                    logger.info(
                        "[create_triframe tool] Saved frame %s using provider '%s' model '%s' to %s",
                        frame_number,
                        result.provider,
                        result.model,
                        filepath,
                    )
                self._animations[animation_id] = saved_paths
                logger.info("[create_triframe tool] Animation '%s' is ready to play.", animation_id)
            except ImageProviderError as exc:
                logger.error("[create_triframe tool] Image provider failed: %s", exc)
            except Exception:
                logger.exception("[create_triframe tool] Failed to generate tri-frame animation")
            finally:
                self.image_tools._set_canvas_activity(False)
                self.image_tools._trigger_after_tool_call("create_triframe")

        thread = threading.Thread(target=_worker, daemon=True)
        self._last_generation_thread = thread
        thread.start()
        name_message = f" with name '{animation_name}'" if animation_name else ""
        return f"Tri-frame animation generation started in background{name_message}. Animation ID: '{animation_id}'. Call play_animation with that ID once it is ready."

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
        """Display a saved three-frame animation on the canvas.

        Args:
            animation_id: The ID returned by create_triframe.
        """
        frame_paths = self._find_animation(animation_id)
        if not frame_paths:
            return f"Error: Animation '{animation_id}' was not found."
        if not self.canvas_state_service:
            return "Error: Canvas state service is unavailable."
        self.canvas_state_service.show_triframe(frame_paths, theater_id=self.active_theater_id)
        self.image_tools._trigger_after_tool_call("play_animation")
        return f"Playing animation '{animation_id}'."

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
                    logger.exception("[create_triframe tool] Image-created callback failed")
            except Exception:
                logger.exception("[create_triframe tool] Image-created callback failed")

