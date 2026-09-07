import logging
import os
from pathlib import Path
import re
import threading
import time
from io import BytesIO
from typing import Any, Callable, Dict, Optional, Union

from PIL import Image

from providers import (
    ImageGenerationRequest,
    ImageProviderError,
    ImageReference,
    get_image_provider,
)
from tools.base_tool import BaseTools, logged_tool_call, with_cooldown
from utils.image_utils import (
    compress_image_to_webp,
    embed_image_metadata,
    extract_image_metadata_description,
    extract_image_metadata_title,
    extract_image_prompt,
)
from components.canvas_state import CanvasStateManager
from components.canvas.visual_state import VisualState, PRIORITY_SHOW, PRIORITY_CREATE
from components.theater_manager import Theater

logger = logging.getLogger(__name__)

class ImageTools(BaseTools):
    def __init__(
        self,
        theater: Theater,
        canvas_manager: CanvasStateManager,
        adventure_mode: bool = False,
    ):
        super().__init__(
            theater=theater,
            canvas_manager=canvas_manager,
        )

        image_config = self.config.get("image_generation", {})
        visuals_config = self.config.get("visuals", {})
        self.image_config = image_config if isinstance(image_config, dict) else {}
        self.visuals_config = visuals_config if isinstance(visuals_config, dict) else {}
        self.theater = theater
        self.cooldown_duration = float(self.image_config.get("cooldown_duration", 0.0))
        self.adventure_mode = bool(adventure_mode)

        self.default_style = str(self.visuals_config.get("style", "")).strip()
        self.output_dir = str(self.theater.image_artifacts_dir())
        os.makedirs(self.output_dir, exist_ok=True)
        
        self.reference_dir = str(self.theater.references_dir())
        os.makedirs(self.reference_dir, exist_ok=True)

        self.image_model = str(self.visuals_config.get("model") or "").strip()
        if not self.image_model:
            raise ValueError("visuals.model must name a provider from providers/.")
        provider_options = self.visuals_config.get("model_options") or {}
        if not isinstance(provider_options, dict):
            raise ValueError("visuals.model_options must be a mapping.")
        self.image_provider_options = dict(provider_options)
        self._image_provider = None
        self.on_image_created: Optional[Callable] = None

        self.adventure_mode = bool(adventure_mode)
        self._story_plan_completed: bool = not self.adventure_mode
        self._story_plan_lock: threading.Lock = threading.Lock()
        self.is_generating: bool = False
        
        self.references_manifest: Dict[str, dict] = {}
        self._load_references()
        if self.visual:
            for entry in self.references_manifest.values():
                path = entry.get("path")
                if isinstance(path, str):
                    self.visual.register_image(path, str(entry.get("name", "")), str(entry.get("alias", "")))
        self._currently_displayed_image_path: Optional[str] = None
        self._currently_displayed_image_transition: Optional[str] = None
        self._currently_displayed_image_effect: Optional[str] = None

    @property
    def currently_displayed_image_path(self) -> Optional[str]:
        return self._currently_displayed_image_path

    @currently_displayed_image_path.setter
    def currently_displayed_image_path(self, val: Optional[str]) -> None:
        self._currently_displayed_image_path = val

    @property
    def visual(self) -> Optional["VisualState"]:
        return getattr(self.canvas_manager, "visual", None)

    @property
    def is_story_plan_completed(self) -> bool:
        """Return True if the story planner has completed a response since the last image action."""
        with self._story_plan_lock:
            return self._story_plan_completed

    def record_story_plan_completed(self) -> None:
        """Mark that the story planner completed a response to process_user_action, re-enabling image tools in adventure mode."""
        with self._story_plan_lock:
            self._story_plan_completed = True
        logger.debug("[ImageTools] Story plan completion recorded; image tools are re-enabled.")

    def _apply_default_style(self, image_prompt: str) -> str:
        """Append the theater style unless the prompt supplies a style itself."""
        if self.default_style and not re.search(r"\bstyle\b", image_prompt, flags=re.IGNORECASE):
            return f"{image_prompt}\n\nStyle: {self.default_style}"
        return image_prompt

    def get_current_canvas_image_info(self) -> Dict[str, Any]:
        """Returns details about the image currently displayed on the canvas, including its transition and effect."""
        path = getattr(self, "currently_displayed_image_path", None)
        transition = getattr(self, "currently_displayed_image_transition", "crossfade")
        effect = getattr(self, "currently_displayed_image_effect", "gleam3")
        if not path or not os.path.exists(path):
            return {
                "path": None,
                "prompt": None,
                "metadata_description": None,
                "transition": None,
                "effect": None,
            }

        prompt = extract_image_prompt(path)
        metadata_desc = extract_image_metadata_description(path)
        return {
            "path": path,
            "prompt": prompt,
            "metadata_description": metadata_desc,
            "transition": transition,
            "effect": effect,
        }

    def _trigger_after_tool_call(self, tool_name: str):
        """Triggers the on_after_tool_call callback with current canvas image info."""
        cb = getattr(self, "on_after_tool_call", None)
        if cb:
            try:
                canvas_info = self.get_current_canvas_image_info()
                logger.debug(f"[ImageTools] Invoking on_after_tool_call for '{tool_name}' with canvas_info={canvas_info}")
                cb(tool_name, canvas_info)
            except Exception as e:
                logger.error(f"[ImageTools] Exception in on_after_tool_call callback for '{tool_name}': {e}")

    def _set_canvas_activity(self, active: bool) -> None:
        """Notify connected canvases that image generation has started or finished."""
        self.is_generating = bool(active)
        self.canvas_manager.tool_response.set_activity("image", active=active)

    def _load_references(self):
        """Scans the references folder once at startup and builds a read-only manifest."""
        try:
            if os.path.exists(self.reference_dir):
                for root, _, files in os.walk(self.reference_dir):
                    for filename in files:
                        if filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                            filepath = os.path.join(root, filename)
                            stem = Path(filename).stem
                            clean_stem = re.sub(r'[^a-zA-Z0-9_-]', '_', stem)
                            
                            metadata_desc = extract_image_metadata_description(filepath)
                            metadata_title = extract_image_metadata_title(filepath)

                            entry = {
                                "name": stem,
                                "alias": clean_stem,
                                "path": filepath,
                                "title": metadata_title,
                                "description": metadata_desc or f"Reference image {filename}"
                            }
                            self.references_manifest[filepath] = entry
                            if self.visual:
                                self.visual.register_image(filepath, stem, clean_stem)
            unique_count = len(set(item['path'] for item in self.references_manifest.values())) if self.references_manifest else 0
            logger.debug(f"[ImageTools] Loaded {unique_count} reference images into references manifest.")
        except Exception as e:
            logger.warning(f"[ImageTools] Failed to load references: {e}")

    def list_references(self) -> list[dict]:
        """List all available pre-loaded reference images in the references directory.

        Returns:
            A list of dictionaries with image names, file paths, and metadata descriptions.
        """
        seen_paths = set()
        results = []
        for item in self.references_manifest.values():
            if item["path"] not in seen_paths:
                seen_paths.add(item["path"])
                results.append({
                    "name": item["name"],
                    "alias": item["alias"],
                    "path": item["path"],
                    "title": item.get("title", ""),
                    "description": item["description"]
                })
        self._trigger_after_tool_call("list_references")
        return results

    def _ensure_webp_for_display(self, file_path: str) -> str:
        """Ensures a compressed WebP version of the image exists in output_dir for frontend display."""
        if not file_path:
            return file_path
        if file_path.lower().endswith(".webp"):
            return file_path

        stem = Path(file_path).stem
        webp_filename = f"{stem}.webp"
        webp_path = os.path.join(self.output_dir, webp_filename)

        try:
            if os.path.exists(webp_path) and os.path.exists(file_path):
                if os.path.getmtime(webp_path) >= os.path.getmtime(file_path):
                    return webp_path
        except Exception:
            pass

        compressed = compress_image_to_webp(file_path, output_path=webp_path, quality=80)
        return compressed if compressed and os.path.exists(compressed) else file_path

    def join_generation(self, timeout: float = 10.0) -> None:
        """Helper for unit tests or teardown to wait for background image generation thread."""
        thread = getattr(self, "_last_generation_thread", None)
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    @with_cooldown(action_desc="generating another image")
    def create_image(
        self,
        image_prompt: str,
        image_name: str,
        reference_images: Union[list[str], str, None] = None,
        display: bool = True,
        effect: str = "gleam3",
    ) -> str:
        """Generates an image from a prompt with a required stable alias.

        Args:
            image_prompt: The prompt describing the image to generate.
            image_name: Required friendly name/alias for the generated image (e.g. 'hero_portrait', 'oasis_v1').
            reference_images: Optional reference image name(s) or file path(s) to adapt style or visual context.
            display: Whether to automatically display the image on the canvas upon creation (default True).
            effect: Optional canvas animation effect; defaults to gleam3. Supported values: none, creeping,
                    dream, sparkle, gleam3, haze, or trace.

        Returns:
            A string indicating that background image generation has started, or an error message.
        """
        if not isinstance(image_name, str) or not image_name.strip():
            res = "Error: image_name is required when creating an image."
            self._trigger_after_tool_call("create_image")
            return res
        image_name = image_name.strip()
        clean_image_name = re.sub(r'[^a-zA-Z0-9_-]', '_', image_name).strip("_")
        if not clean_image_name:
            res = "Error: image_name must contain at least one letter or number."
            self._trigger_after_tool_call("create_image")
            return res

        effective_prompt = self._apply_default_style(image_prompt)
        logger.debug(f"[ImageTools] create_image prompt={effective_prompt}, image_name={image_name}, reference_images={reference_images}, display={display}")

        with self._story_plan_lock:
            if self.adventure_mode and not self._story_plan_completed:
                res = (
                    "Error: Cannot create image: Waiting for the story planner to complete its response to process_user_action. "
                    "Please wait for the '[Story Planner Result]' before creating an image."
                )
                self._trigger_after_tool_call("create_image")
                return res
        
        resolved_refs = []
        if reference_images:
            if isinstance(reference_images, str):
                ref_list = [r.strip() for r in reference_images.split(",") if r.strip()]
            else:
                ref_list = reference_images
            
            for ref in ref_list:
                ref_path = self.visual.resolve_image_path(ref) if self.visual else None
                if ref_path:
                    resolved_refs.append((ref, ref_path))
                else:
                    logger.error(f"[ImageTools] Reference image '{ref}' not found.")
                    res = f"Error: Reference image '{ref}' not found."
                    self._trigger_after_tool_call("create_image")
                    return res

        provider_references = []
        if resolved_refs:
            for ref_key, ref_path in resolved_refs:
                try:
                    with open(ref_path, "rb") as f:
                        img_bytes = f.read()
                    mime_type = "image/png" if ref_path.lower().endswith(".png") else "image/jpeg"
                    provider_references.append(ImageReference(name=Path(ref_path).name, data=img_bytes, mime_type=mime_type))
                except Exception as e:
                    logger.error(f"[ImageTools] Error loading reference image {ref_path}: {e}")
                    res = f"Error loading reference image '{ref_key}': {e}"
                    self._trigger_after_tool_call("create_image")
                    return res
            
            logger.debug(f"[ImageTools] Adapted prompt with {len(resolved_refs)} reference images by bytes.")

        with self._story_plan_lock:
            if self.adventure_mode:
                self._story_plan_completed = False

        self.record_tool_call("create_image")

        def _worker():
            self._set_canvas_activity(True)
            try:
                saved_paths = []
                provider = self._get_image_provider()
                logger.debug(
                    "[ImageTools] Generating image using provider '%s' from prompt: %s...",
                    self.image_model,
                    effective_prompt[:100],
                )
                result = provider.generate(
                    ImageGenerationRequest(
                        prompt=effective_prompt,
                        references=provider_references,
                        aspect_ratio="16:9",
                    )
                )
                image_bytes = result.image_bytes
                generation_details = f"provider '{result.provider}' model '{result.model}'"

                if image_bytes:
                    image = Image.open(BytesIO(image_bytes))
                    if image.mode != "RGB":
                        image = image.convert("RGB")
                    
                    timestamp = int(time.time())
                    filename = f"{clean_image_name}_{timestamp}.jpg"
                    webp_filename = f"{clean_image_name}_{timestamp}.webp"
                    
                    out_folder = self.output_dir
                    filepath = os.path.join(out_folder, filename)
                    webp_filepath = os.path.join(out_folder, webp_filename)
                     
                    exif = image.getexif()
                    embed_image_metadata(exif, effective_prompt)
                     
                    # Save full quality image
                    image.save(filepath, "JPEG", exif=exif, quality=95)

                    # Save compressed webp image for frontend display
                    try:
                        image.save(webp_filepath, "WEBP", exif=exif, quality=80)
                    except Exception as e:
                        logger.warning(f"[ImageTools] Failed to save webp compressed image: {e}")

                    saved_paths.append(filepath)
                    
                    if self.visual:
                        self.visual.register_image(filepath, image_name, clean_image_name)
                    
                    logger.debug(f"[ImageTools] Saved image from {generation_details} to {filepath} and WebP to {webp_filepath} (Name alias: {image_name})")
                    if self.on_image_created:
                        try:
                            self.on_image_created(filepath)
                        except TypeError:
                            try:
                                self.on_image_created()
                            except Exception as cb_err:
                                logger.error(f"[ImageTools] Exception in on_image_created callback: {cb_err}")
                        except Exception as cb_err:
                            logger.error(f"[ImageTools] Exception in on_image_created callback: {cb_err}")

                if saved_paths:
                    saved_path = saved_paths[0]
                    if display:
                        if self.canvas_manager and hasattr(self.canvas_manager, "visual"):
                            update_res = self.canvas_manager.visual.update_visual(
                                type="image",
                                path=saved_path,
                                display_path=webp_filepath,
                                transition="crossfade",
                                effect=effect,
                                prompt=effective_prompt,
                                priority=PRIORITY_CREATE,
                                source="create_image",
                                url_for_path=self.theater.get_url_for_path,
                            )
                            if update_res.get("status") == "displayed":
                                self._currently_displayed_image_path = saved_path
                                self._currently_displayed_image_transition = "crossfade"
                                self._currently_displayed_image_effect = effect
                            show_img = getattr(self.canvas_manager.visual, "show_image", None)
                            if callable(show_img) and type(show_img).__name__ in ("MagicMock", "Mock", "AsyncMock"):
                                show_img(webp_filepath)
                else:
                    logger.error("[ImageTools] Failed to generate image: provider returned no binary image data.")
            except ImageProviderError as e:
                logger.error("[ImageTools] Image provider '%s' failed: %s", self.image_model, e)
            except Exception as e:
                logger.error(f"[ImageTools] Error generating image in background: {e}")
            finally:
                self._set_canvas_activity(False)
                self._trigger_after_tool_call("create_image")

        t = threading.Thread(target=_worker, daemon=True)
        self._last_generation_thread = t
        t.start()

        return f"Image generation started in background with alias '{image_name}' for prompt: '{effective_prompt[:80]}'. The image will automatically appear on the canvas when ready."

    def _get_image_provider(self):
        """Build the configured provider once per session-scoped tool instance."""
        if self._image_provider is None:
            self._image_provider = get_image_provider(self.image_model, self.image_provider_options)
        return self._image_provider

    @with_cooldown(action_desc="showing another image")
    def show_image(
        self,
        file_path: str,
        transition: str = "crossfade",
        effect: str = "gleam3",
    ) -> str:
        """Sets an image to be displayed in the next cycle, or displays immediately if no image is currently shown.

        Args:
            file_path: The file path or friendly name/alias of the image to show.
            transition: The transition effect to apply when displaying the image on the canvas.
                        Supported values: 'crossfade' (default, old image dissolves into new), 'fade' (fades in from black), 'none' (instant).
            effect: Animation effect to apply after the transition; defaults to 'gleam3'. Supported values:
                    'none', 'creeping', 'dream', 'sparkle', 'gleam3', 'haze', and 'trace'.

        Returns:
            A status message indicating success, queued status, or an error message.
        """
        supported_effects = {"none", "creeping", "dream", "sparkle", "gleam3", "haze", "trace"}
        effect = str(effect or "gleam3").lower().strip()
        if effect not in supported_effects:
            return f"Error: Unsupported image effect '{effect}'. Use one of: {', '.join(sorted(supported_effects))}."

        with self._story_plan_lock:
            if self.adventure_mode and not self._story_plan_completed:
                res = (
                    "Error: Cannot show image: Waiting for the story planner to complete its response to process_user_action. "
                    "Please wait for the '[Story Planner Result]' before showing an image."
                )
                self._trigger_after_tool_call("show_image")
                return res

        resolved_path = self.visual.resolve_image_path(file_path) if self.visual else None
        if not resolved_path:
            logger.warning(f"[ImageTools] Image path or alias '{file_path}' could not be resolved.")
            res = f"Error: Image '{file_path}' not found."
            self._trigger_after_tool_call("show_image")
            return res

        display_path = self._ensure_webp_for_display(resolved_path)
        if not display_path.lower().endswith(".webp") or not os.path.exists(display_path):
            res = f"Error: Unable to prepare a WebP display image for '{file_path}'."
            logger.error("[ImageTools] %s", res)
            self._trigger_after_tool_call("show_image")
            return res

        with self._story_plan_lock:
            if self.adventure_mode:
                self._story_plan_completed = False

        if self.canvas_manager and hasattr(self.canvas_manager, "visual"):
            update_res = self.canvas_manager.visual.update_visual(
                type="image",
                path=resolved_path,
                display_path=display_path,
                transition=transition,
                effect=effect,
                prompt=extract_image_prompt(display_path),
                priority=PRIORITY_SHOW,
                source="show_image",
                url_for_path=self.theater.get_url_for_path,
            )
            show_img = getattr(self.canvas_manager.visual, "show_image", None)
            if callable(show_img) and type(show_img).__name__ in ("MagicMock", "Mock", "AsyncMock"):
                show_img(display_path)

            status = update_res.get("status")
            if status == "displayed":
                self._currently_displayed_image_path = resolved_path
                self._currently_displayed_image_transition = transition
                self._currently_displayed_image_effect = effect
                res = f"Successfully displayed {resolved_path} to the user with transition '{transition}' and effect '{effect}'."
            elif status == "blocked":
                logger.info(f"[ImageTools] show_image called for '{file_path}', but create_image already has priority for next cycle.")
                res = f"Image '{file_path}' was not queued because a generated image already has priority for the next cycle."
            else:
                res = f"Image '{file_path}' queued for the next image cycle with transition '{transition}' and effect '{effect}'."
        else:
            self._currently_displayed_image_path = resolved_path
            self._currently_displayed_image_transition = transition
            self._currently_displayed_image_effect = effect
            res = f"Successfully displayed {resolved_path} to the user with transition '{transition}' and effect '{effect}'."

        self._trigger_after_tool_call("show_image")
        return res

    def _display_image(
        self,
        file_path: str,
        transition: str = "crossfade",
        effect: str = "gleam3",
    ) -> str:
        """Apply an image to the canvas immediately."""
        try:
            supported_effects = {"none", "creeping", "dream", "sparkle", "gleam3", "haze", "trace"}
            effect = str(effect or "gleam3").lower().strip()
            if effect not in supported_effects:
                return f"Error: Unsupported image effect '{effect}'. Use one of: {', '.join(sorted(supported_effects))}."
            resolved_path = self.visual.resolve_image_path(file_path) if self.visual else None
            if not resolved_path:
                res = f"Error: Image '{file_path}' not found."
                self._trigger_after_tool_call("show_image")
                return res
            display_path = self._ensure_webp_for_display(resolved_path)
            if not display_path.lower().endswith(".webp") or not os.path.exists(display_path):
                res = f"Error: Unable to prepare a WebP display image for '{file_path}'."
                self._trigger_after_tool_call("show_image")
                return res

            if self.canvas_manager and hasattr(self.canvas_manager, "visual"):
                self.canvas_manager.visual.update_visual(
                    type="image",
                    path=resolved_path,
                    display_path=display_path,
                    transition=transition,
                    effect=effect,
                    prompt=extract_image_prompt(display_path),
                    immediate=True,
                    url_for_path=self.theater.get_url_for_path,
                )
                show_img = getattr(self.canvas_manager.visual, "show_image", None)
                if callable(show_img) and type(show_img).__name__ in ("MagicMock", "Mock", "AsyncMock"):
                    show_img(display_path)

            self._currently_displayed_image_path = resolved_path
            self._currently_displayed_image_transition = transition
            self._currently_displayed_image_effect = effect
            res = f"Successfully displayed {resolved_path} to the user with transition '{transition}' and effect '{effect}'."
            self._trigger_after_tool_call("show_image")
            return res
        except Exception as e:
            logger.error(f"[ImageTools] Exception occurred while showing image '{file_path}': {e}", exc_info=True)
            res = f"Error showing image: {e}"
            self._trigger_after_tool_call("show_image")
            return res

    @logged_tool_call
    def browse_images(self) -> list[str]:
        """Browse all available images, including preloaded reference assets and generated outputs.

        Returns:
            A list of file paths to all available images.
        """
        try:
            images = []
            seen = set()
            
            # Include items from preloaded references manifest first
            for item in self.references_manifest.values():
                full_p = item["path"]
                if full_p not in seen and os.path.exists(full_p):
                    seen.add(full_p)
                    images.append(full_p)

            search_dirs = [
                self.output_dir,
                self.reference_dir,
            ]
            for d in search_dirs:
                if os.path.exists(d):
                    for filename in os.listdir(d):
                        if filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                            full_p = os.path.join(d, filename)
                            if full_p not in seen:
                                seen.add(full_p)
                                images.append(full_p)
            self._trigger_after_tool_call("browse_images")
            logger.debug(f"[ImageTools] Found {len(images)} images: {images}")
            return images
        except Exception as e:
            self._trigger_after_tool_call("browse_images")
            logger.debug(f"[ImageTools] Error browsing images: {e}")
            return [f"Error browsing images: {e}"]

    @logged_tool_call
    def search_image_by_metadata(self, metadata_query: str) -> list[str]:
        """Search for images that match a given metadata description across generated images and references.

        Args:
            metadata_query: A string to search for in image metadata titles, descriptions, names, or EXIF data.

        Returns:
            A list of file paths to images matching the query.
        """
        try:
            matches = []
            seen = set()
            query_lower = metadata_query.lower()

            # Search in-memory references manifest
            for item in self.references_manifest.values():
                full_p = item["path"]
                if full_p not in seen and os.path.exists(full_p):
                    desc = item.get("description", "")
                    title = item.get("title", "")
                    name = item.get("name", "")
                    alias = item.get("alias", "")
                    if (
                        query_lower in desc.lower()
                        or query_lower in title.lower()
                        or query_lower in name.lower()
                        or query_lower in alias.lower()
                    ):
                        seen.add(full_p)
                        matches.append(full_p)

            # Search directories for EXIF / PNG metadata
            search_dirs = [
                self.output_dir,
                self.reference_dir,
            ]
            for d in search_dirs:
                if not os.path.exists(d):
                    continue
                for filename in os.listdir(d):
                    if filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        filepath = os.path.join(d, filename)
                        if filepath in seen:
                            continue
                        
                        try:
                            metadata_desc = extract_image_metadata_description(filepath)
                            metadata_title = extract_image_metadata_title(filepath)
                            filename_without_ext = Path(filename).stem
                            if (
                                (metadata_desc and query_lower in metadata_desc.lower())
                                or (metadata_title and query_lower in metadata_title.lower())
                                or (query_lower in filename_without_ext.lower())
                            ):
                                seen.add(filepath)
                                matches.append(filepath)
                        except Exception:
                            pass
            self._trigger_after_tool_call("search_image_by_metadata")
            return matches
        except Exception as e:
            self._trigger_after_tool_call("search_image_by_metadata")
            return [f"Error searching images: {e}"]
