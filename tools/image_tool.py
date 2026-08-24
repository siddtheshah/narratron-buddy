import logging
import os
from pathlib import Path
import re
import threading
import time
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional, Union

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
    extract_image_prompt,
)
from components.theater_manager import TheaterManager

logger = logging.getLogger(__name__)

class ImageTools(BaseTools):
    _references_cache: Dict[str, dict] = {}
    _reference_dir_cached: Optional[str] = None

    def __init__(
        self,
        config: dict,
        theater_id: str,
        theater_manager: TheaterManager,
        canvas_state_service: Any = None,
        adventure_mode: bool = False,
    ):
        raw_config = config or {}
        subconfig = raw_config.get("image_generation", raw_config) if "image_generation" in raw_config else raw_config
        super().__init__(
            config=subconfig,
            theater_id=theater_id,
            canvas_state_service=canvas_state_service,
            default_cooldown=60.0,
        )

        self.theater_manager = theater_manager
        self.theater = theater_manager.theater(self.active_theater_id)
        self.default_style = str(subconfig.get("style", "")).strip()
        self.output_dir = str(self.theater.image_artifacts_dir())
        os.makedirs(self.output_dir, exist_ok=True)
        
        self.reference_dir = str(self.theater.references_dir())
        os.makedirs(self.reference_dir, exist_ok=True)

        self.image_provider_id = str(subconfig.get("provider") or "").strip()
        if not self.image_provider_id:
            raise ValueError("image_generation.provider must name a provider from providers/.")
        provider_options = subconfig.get("provider_options") or {}
        if not isinstance(provider_options, dict):
            raise ValueError("image_generation.provider_options must be a mapping.")
        self.image_provider_options = dict(provider_options)
        self._image_provider = None

        self.on_show_image = None
        self.on_image_created: Optional[Callable] = None

        self.currently_displayed_image_path: Optional[str] = None
        self.currently_displayed_image_transition: str = "crossfade"
        self.currently_displayed_image_effect: str = "gleam3"

        # In-memory mapping of custom image names/aliases to file paths
        self.image_aliases: Dict[str, str] = {}

        # Image cycle configuration and state
        self.cycle_length: float = float(subconfig.get("cycle_length", subconfig.get("cooldown_duration", 20.0)))
        self._cycle_lock = threading.RLock()
        self._cycle_timer: Optional[threading.Timer] = None
        self._cycle_active: bool = False

        self.current_cycle_image: Optional[dict] = None
        self.next_cycle_image: Optional[dict] = None

        self.PRIORITY_SHOW = 1
        self.PRIORITY_CREATE = 2

        self.adventure_mode = bool(adventure_mode)
        self._story_plan_completed: bool = not self.adventure_mode
        self._story_plan_lock: threading.Lock = threading.Lock()
        
        # Reuse cached references manifest if directory hasn't changed
        if ImageTools._reference_dir_cached == self.reference_dir and ImageTools._references_cache:
            self.references_manifest = ImageTools._references_cache
        else:
            self.references_manifest = {}
            self._load_references()
            ImageTools._references_cache = self.references_manifest
            ImageTools._reference_dir_cached = self.reference_dir

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
        if self.canvas_state_service:
            self.canvas_state_service.set_tool_activity(
                "image", active=active, theater_id=self.active_theater_id
            )

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

                            entry = {
                                "name": stem,
                                "alias": clean_stem,
                                "path": filepath,
                                "description": metadata_desc or f"Reference image {filename}"
                            }
                            self.references_manifest[stem] = entry
                            self.references_manifest[clean_stem] = entry
                            self.references_manifest[stem.lower()] = entry
                            self.references_manifest[clean_stem.lower()] = entry
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
                    "description": item["description"]
                })
        self._trigger_after_tool_call("list_references")
        return results

    def _find_image_path(self, path_str: str) -> Optional[str]:
        if not path_str:
            return None
        
        # Check in-memory alias dictionary
        if path_str in self.image_aliases:
            return self.image_aliases[path_str]
        clean_input = re.sub(r'[^a-zA-Z0-9_-]', '_', path_str)
        if clean_input in self.image_aliases:
            return self.image_aliases[clean_input]

        # Check references manifest
        if path_str in self.references_manifest:
            return self.references_manifest[path_str]["path"]
        if path_str.lower() in self.references_manifest:
            return self.references_manifest[path_str.lower()]["path"]
        if clean_input in self.references_manifest:
            return self.references_manifest[clean_input]["path"]
        if clean_input.lower() in self.references_manifest:
            return self.references_manifest[clean_input.lower()]["path"]


        base_name = os.path.basename(path_str)
        search_dirs = [
            self.reference_dir,
            self.output_dir,
        ]

        for directory in search_dirs:
            candidates = [
                os.path.join(directory, path_str),
                os.path.join(directory, base_name),
                os.path.join(directory, f"{path_str}.jpg"),
                os.path.join(directory, f"{path_str}.png"),
                os.path.join(directory, f"{path_str}.webp"),
            ]
            for candidate in candidates:
                if os.path.exists(candidate):
                    return candidate

        return None

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

    @with_cooldown("generating another image")
    def create_image(
        self,
        image_prompt: str,
        image_name: Optional[str] = None,
        reference_images: Union[list[str], str, None] = None,
        display: bool = True,
        effect: str = "gleam3",
    ) -> str:
        """Generates an image from a prompt, supporting custom image naming and reference image adaptation.

        Args:
            image_prompt: The prompt describing the image to generate.
            image_name: Optional friendly name/alias for the generated image (e.g. 'hero_portrait', 'oasis_v1').
            reference_images: Optional reference image name(s) or file path(s) to adapt style or visual context.
            display: Whether to automatically display the image on the canvas upon creation (default True).
            effect: Optional canvas animation effect; defaults to gleam3. Supported values: none, creeping,
                    dream, sparkle, gleam3, bendy, haze, or trace.

        Returns:
            A string indicating that background image generation has started, or an error message.
        """
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
                ref_path = self._find_image_path(ref)
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
                    self.image_provider_id,
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
                    if image_name:
                        clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', image_name)
                        filename = f"{clean_name}_{timestamp}.jpg"
                        webp_filename = f"{clean_name}_{timestamp}.webp"
                    else:
                        filename = f"image_{timestamp}_0.jpg"
                        webp_filename = f"image_{timestamp}_0.webp"
                    
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
                    
                    # Register alias
                    if image_name:
                        clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', image_name)
                        self.image_aliases[image_name] = filepath
                        self.image_aliases[clean_name] = filepath
                        self.image_aliases[image_name.lower()] = filepath
                        self.image_aliases[clean_name.lower()] = filepath
                    
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
                        with self._cycle_lock:
                            if self.current_cycle_image is None and not self.currently_displayed_image_path:
                                self.current_cycle_image = {
                                    "path": saved_path,
                                    "transition": "crossfade",
                                    "effect": effect,
                                    "priority": self.PRIORITY_CREATE,
                                    "source": "create_image",
                                }
                                self._display_image(saved_path, transition="crossfade", effect=effect)
                                self._schedule_next_cycle_tick()
                            else:
                                self.next_cycle_image = {
                                    "path": saved_path,
                                    "transition": "crossfade",
                                    "effect": effect,
                                    "priority": self.PRIORITY_CREATE,
                                    "source": "create_image",
                                }
                                if not self._cycle_active and self.cycle_length > 0:
                                    self._schedule_next_cycle_tick()
                                logger.info(f"[ImageTools] Generated image queued with priority for the next cycle: {saved_path}")
                else:
                    logger.error("[ImageTools] Failed to generate image: provider returned no binary image data.")
            except ImageProviderError as e:
                logger.error("[ImageTools] Image provider '%s' failed: %s", self.image_provider_id, e)
            except Exception as e:
                logger.error(f"[ImageTools] Error generating image in background: {e}")
            finally:
                self._set_canvas_activity(False)
                self._trigger_after_tool_call("create_image")

        t = threading.Thread(target=_worker, daemon=True)
        self._last_generation_thread = t
        t.start()

        name_msg = f" with alias '{image_name}'" if image_name else ""
        return f"Image generation started in background{name_msg} for prompt: '{effective_prompt[:80]}'. The image will automatically appear on the canvas when ready."

    def _get_image_provider(self):
        """Build the configured provider once per session-scoped tool instance."""
        if self._image_provider is None:
            self._image_provider = get_image_provider(self.image_provider_id, self.image_provider_options)
        return self._image_provider

    def advance_cycle(self) -> Optional[dict]:
        """Advance to the next image cycle.

        If next_cycle_image is set, promote it to current_cycle_image,
        display it on the canvas, and clear next_cycle_image.
        If next_cycle_image is None, reuse the current image.
        Returns the new current_cycle_image.
        """
        with self._cycle_lock:
            if self.next_cycle_image is not None:
                staged = self.next_cycle_image
                self.next_cycle_image = None
                self.current_cycle_image = staged
                path = staged["path"]
                transition = staged.get("transition", "crossfade")
                effect = staged.get("effect", "gleam3")
                logger.info(f"[ImageTools] Cycle rollover: displaying new image {path} (source={staged.get('source')})")
                self._display_image(path, transition=transition, effect=effect)
            else:
                logger.debug("[ImageTools] Cycle rollover: no next image staged, retaining current image.")

            return self.current_cycle_image

    def _schedule_next_cycle_tick(self):
        with self._cycle_lock:
            if self._cycle_timer:
                self._cycle_timer.cancel()
                self._cycle_timer = None
            if self.cycle_length > 0:
                self._cycle_timer = threading.Timer(self.cycle_length, self._on_cycle_tick)
                self._cycle_timer.daemon = True
                self._cycle_timer.start()
                self._cycle_active = True

    def stop_cycle(self):
        """Stop the background image cycle timer."""
        with self._cycle_lock:
            if self._cycle_timer:
                self._cycle_timer.cancel()
                self._cycle_timer = None
            self._cycle_active = False

    def __del__(self):
        try:
            self.stop_cycle()
        except Exception:
            pass

    def _on_cycle_tick(self):
        try:
            self.advance_cycle()
        finally:
            with self._cycle_lock:
                if self._cycle_active and self.cycle_length > 0:
                    self._schedule_next_cycle_tick()

    @with_cooldown("showing another image")
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
                    'none', 'creeping', 'dream', 'sparkle', 'gleam3', 'bendy', 'haze', and 'trace'.

        Returns:
            A status message indicating success, queued status, or an error message.
        """
        supported_effects = {"none", "creeping", "dream", "sparkle", "gleam3", "bendy", "haze", "trace"}
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

        resolved_path = self._find_image_path(file_path)
        if not resolved_path:
            logger.warning(f"[ImageTools] Image path or alias '{file_path}' could not be resolved.")
            res = f"Error: Image '{file_path}' not found."
            self._trigger_after_tool_call("show_image")
            return res

        with self._story_plan_lock:
            if self.adventure_mode:
                self._story_plan_completed = False

        with self._cycle_lock:
            # If no image is currently displayed (cold start), display immediately and start cycle
            if self.current_cycle_image is None and not self.currently_displayed_image_path:
                self.current_cycle_image = {
                    "path": resolved_path,
                    "transition": transition,
                    "effect": effect,
                    "priority": self.PRIORITY_SHOW,
                    "source": "show_image",
                }
                res = self._display_image(resolved_path, transition=transition, effect=effect)
                self._schedule_next_cycle_tick()
                return res

            # If next_cycle_image already has a higher priority (create_image), do not override
            if self.next_cycle_image and self.next_cycle_image.get("priority", 0) >= self.PRIORITY_CREATE:
                logger.info(f"[ImageTools] show_image called for '{file_path}', but create_image already has priority for next cycle.")
                self._trigger_after_tool_call("show_image")
                return f"Image '{file_path}' was not queued because a generated image already has priority for the next cycle."

            self.next_cycle_image = {
                "path": resolved_path,
                "transition": transition,
                "effect": effect,
                "priority": self.PRIORITY_SHOW,
                "source": "show_image",
            }
            if not self._cycle_active and self.cycle_length > 0:
                self._schedule_next_cycle_tick()

            self._trigger_after_tool_call("show_image")
            return f"Image '{file_path}' queued for the next image cycle with transition '{transition}' and effect '{effect}'."

    def _display_image(
        self,
        file_path: str,
        transition: str = "crossfade",
        effect: str = "gleam3",
    ) -> str:
        """Apply an image to the canvas."""
        try:
            supported_effects = {"none", "creeping", "dream", "sparkle", "gleam3", "bendy", "haze", "trace"}
            effect = str(effect or "gleam3").lower().strip()
            if effect not in supported_effects:
                return f"Error: Unsupported image effect '{effect}'. Use one of: {', '.join(sorted(supported_effects))}."
            logger.debug(f"[ImageTools] Showing image file_path='{file_path}', transition='{transition}', effect='{effect}'")

            resolved_path = self._find_image_path(file_path)
            logger.debug(f"[ImageTools] Resolved image '{file_path}' to '{resolved_path}' (transition='{transition}')")
            if resolved_path:
                display_path = self._ensure_webp_for_display(resolved_path)
                canvas_state_service = getattr(self, "canvas_state_service", None)
                if canvas_state_service:
                    canvas_state_service.show_image(
                        display_path,
                        theater_id=self.active_theater_id,
                        transition=transition,
                        effect=effect,
                    )
                if self.on_show_image:
                    logger.debug(f"[ImageTools] Invoking on_show_image callback with '{resolved_path}', transition='{transition}', effect='{effect}'")
                    try:
                        self.on_show_image(
                            resolved_path,
                            transition=transition,
                            effect=effect,
                        )
                    except Exception as e:
                        logger.error(f"[ImageTools] Exception in on_show_image callback: {e}")
                elif not canvas_state_service:
                    logger.warning("[ImageTools] on_show_image callback is not set")
                self.currently_displayed_image_path = resolved_path
                self.currently_displayed_image_transition = transition
                self.currently_displayed_image_effect = effect
                res = f"Successfully displayed {resolved_path} to the user with transition '{transition}' and effect '{effect}'."
            else:
                logger.warning(f"[ImageTools] Image path or alias '{file_path}' could not be resolved.")
                res = f"Error: Image '{file_path}' not found."
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
            return images
        except Exception as e:
            self._trigger_after_tool_call("browse_images")
            return [f"Error browsing images: {e}"]

    @logged_tool_call
    def search_image_by_metadata(self, metadata_query: str) -> list[str]:
        """Search for images that match a given metadata description across generated images and references.

        Args:
            metadata_query: A string to search for in image metadata descriptions, names, or EXIF data.

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
                    name = item.get("name", "")
                    alias = item.get("alias", "")
                    if query_lower in desc.lower() or query_lower in name.lower() or query_lower in alias.lower():
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
                            filename_without_ext = Path(filename).stem
                            if (metadata_desc and query_lower in metadata_desc.lower()) or (query_lower in filename_without_ext.lower()):
                                seen.add(filepath)
                                matches.append(filepath)
                        except Exception:
                            pass
            self._trigger_after_tool_call("search_image_by_metadata")
            return matches
        except Exception as e:
            self._trigger_after_tool_call("search_image_by_metadata")
            return [f"Error searching images: {e}"]
