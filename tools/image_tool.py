import base64
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import threading
import time
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional, Union

from google import genai
from google.genai import types
from PIL import Image

from tools.base_tool import BaseTools, with_cooldown
from utils.image_utils import (
    embed_image_metadata,
    extract_image_metadata_description,
    extract_image_prompt,
)
from components.theater_manager import TheaterManager

logger = logging.getLogger(__name__)

class ImageTools(BaseTools):
    _client_cache = None
    _references_cache: Dict[str, dict] = {}
    _reference_dir_cached: Optional[str] = None

    def __init__(
        self,
        config: dict,
        theater_id: str,
        theater_manager: TheaterManager,
        canvas_state_service: Any = None,
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
        self.default_style = str(raw_config.get("agent", {}).get("style", "")).strip()
        self.output_dir = str(self.theater.image_artifacts_dir())
        os.makedirs(self.output_dir, exist_ok=True)
        
        self.reference_dir = str(self.theater.references_dir())
        os.makedirs(self.reference_dir, exist_ok=True)
        
        # Reuse shared genai Client instance across theater re-initializations
        if ImageTools._client_cache is None:
            project_id = raw_config.get("gcloud", {}).get("project_id", os.getenv("GOOGLE_CLOUD_PROJECT"))
            location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
            ImageTools._client_cache = genai.Client(vertexai=True, project=project_id, location=location)
        
        self.client = ImageTools._client_cache

        self.on_show_image = None
        self.on_image_created: Optional[Callable] = None

        self.currently_displayed_image_path: Optional[str] = None
        self.currently_displayed_image_transition: str = "crossfade"
        self.currently_displayed_image_effect: str = "gleam3"

        # Can be hardcoded for now.
        self.simple_model = "gemini-3.1-flash-lite-image"  
        self.reference_model = "gemini-3.1-flash-lite-image"

        # In-memory mapping of custom image names/aliases to file paths
        self.image_aliases: Dict[str, str] = {}
        
        # Reuse cached references manifest if directory hasn't changed
        if ImageTools._reference_dir_cached == self.reference_dir and ImageTools._references_cache:
            self.references_manifest = ImageTools._references_cache
        else:
            self.references_manifest = {}
            self._load_references()
            ImageTools._references_cache = self.references_manifest
            ImageTools._reference_dir_cached = self.reference_dir

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
            logger.info(f"[ImageTools] Loaded {unique_count} reference images into references manifest.")
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
            ]
            for candidate in candidates:
                if os.path.exists(candidate):
                    return candidate

        return None

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
        logging.info(f"[create_image_tool] image_prompt: {effective_prompt}, image_name: {image_name}, reference_images: {reference_images}, display: {display}")
        
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
                    logger.error(f"[create_image tool] Reference image '{ref}' not found.")
                    res = f"Error: Reference image '{ref}' not found."
                    self._trigger_after_tool_call("create_image")
                    return res

        prompt_parts = []
        if resolved_refs:
            for ref_key, ref_path in resolved_refs:
                try:
                    with open(ref_path, "rb") as f:
                        img_bytes = f.read()
                    mime_type = "image/png" if ref_path.lower().endswith(".png") else "image/jpeg"
                    prompt_parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime_type))
                except Exception as e:
                    logger.error(f"[create_image tool] Error loading reference image {ref_path}: {e}")
                    res = f"Error loading reference image '{ref_key}': {e}"
                    self._trigger_after_tool_call("create_image")
                    return res
            
            logger.info(f"[create_image tool] Adapted prompt with {len(resolved_refs)} reference images by bytes.")

        prompt_parts.append(effective_prompt)
        model_name = self.reference_model if resolved_refs else self.simple_model

        self.record_tool_call("create_image")

        def _worker():
            self._set_canvas_activity(True)
            try:
                logger.info(f"[create_image tool] Generating image in background using model '{model_name}' from prompt: {effective_prompt[:100]}...")
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=prompt_parts,
                )
                
                saved_paths = []
                image_bytes = None
                text_feedback = []
                
                if getattr(response, "candidates", None) and response.candidates:
                    candidate = response.candidates[0]
                    content = candidate.get("content") if isinstance(candidate, dict) else getattr(candidate, "content", None)
                    parts = content.get("parts") if isinstance(content, dict) else (getattr(content, "parts", None) if content else None)
                    if parts:
                        for part in parts:
                            inline_data = part.get("inline_data") if isinstance(part, dict) else getattr(part, "inline_data", None)
                            text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
                            
                            if inline_data:
                                raw_data = inline_data.get("data") if isinstance(inline_data, dict) else getattr(inline_data, "data", None)
                                if raw_data:
                                    if isinstance(raw_data, str):
                                        try:
                                            image_bytes = base64.b64decode(raw_data)
                                        except Exception:
                                            image_bytes = raw_data.encode("utf-8")
                                    else:
                                        image_bytes = raw_data
                                    break
                            elif text:
                                text_feedback.append(str(text))
                            
                if image_bytes is not None:
                    image = Image.open(BytesIO(image_bytes))
                    if image.mode != "RGB":
                        image = image.convert("RGB")
                    
                    timestamp = int(time.time())
                    if image_name:
                        clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', image_name)
                        filename = f"{clean_name}_{timestamp}.jpg"
                    else:
                        filename = f"image_{timestamp}_0.jpg"
                    
                    out_folder = self.output_dir
                    filepath = os.path.join(out_folder, filename)
                     
                    exif = image.getexif()
                    embed_image_metadata(exif, effective_prompt)
                     
                    image.save(filepath, "JPEG", exif=exif, quality=95)
                    saved_paths.append(filepath)
                    
                    # Register alias
                    if image_name:
                        clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', image_name)
                        self.image_aliases[image_name] = filepath
                        self.image_aliases[clean_name] = filepath
                        self.image_aliases[image_name.lower()] = filepath
                        self.image_aliases[clean_name.lower()] = filepath
                    
                    logger.info(f"[create_image tool] Saved image in background to {filepath} (Name alias: {image_name})")
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
                    if display:
                        show_res = self.show_image(saved_paths[0], effect=effect)
                        if show_res.startswith("Error:"):
                            logger.warning(f"[create_image tool] Generated image but could not display: {show_res}")
                else:
                    details = ""
                    if text_feedback:
                        details = f" Details: {' '.join(text_feedback)}"
                    elif getattr(response, "candidates", None) and response.candidates:
                        cand = response.candidates[0]
                        finish_reason = cand.get("finish_reason") if isinstance(cand, dict) else getattr(cand, "finish_reason", None)
                        if finish_reason:
                            details = f" Finish reason: {finish_reason}"
                    logger.error(f"[create_image tool] Failed to generate image: Model didn't return binary image data.{details}")
            except Exception as e:
                logger.error(f"[create_image tool] Error generating image in background: {e}")
            finally:
                self._set_canvas_activity(False)
                self._trigger_after_tool_call("create_image")

        t = threading.Thread(target=_worker, daemon=True)
        self._last_generation_thread = t
        t.start()

        name_msg = f" with alias '{image_name}'" if image_name else ""
        return f"Image generation started in background{name_msg} for prompt: '{effective_prompt[:80]}'. The image will automatically appear on the canvas when ready."

    @with_cooldown("showing another image", duration=4.0)
    def show_image(
        self,
        file_path: str,
        transition: str = "crossfade",
        effect: str = "gleam3",
    ) -> str:
        """Shows an image from a file path or friendly name to the user with a specified canvas transition effect.

        Args:
            file_path: The file path or friendly name/alias of the image to show.
            transition: The transition effect to apply when displaying the image on the canvas.
                        Supported values: 'crossfade' (default, old image dissolves into new), 'fade' (fades in from black), 'none' (instant).
            effect: Animation effect to apply after the transition; defaults to 'gleam3'. Supported values:
                    'none', 'creeping', 'dream', 'sparkle', 'gleam3', 'bendy', 'haze', and 'trace'.

        Returns:
            A status message indicating success or failure.
        """
        return self._display_image(file_path, transition=transition, effect=effect)

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
            logger.info(f"[show_image tool] Called — file_path='{file_path}', transition='{transition}', effect='{effect}'")

            resolved_path = self._find_image_path(file_path)
            logger.info(f"[show_image tool] Showing image from '{file_path}' (resolved: '{resolved_path}', transition: '{transition}')")
            if resolved_path:
                canvas_state_service = getattr(self, "canvas_state_service", None)
                if canvas_state_service:
                    canvas_state_service.show_image(
                        resolved_path,
                        theater_id=self.active_theater_id,
                        transition=transition,
                        effect=effect,
                    )
                if self.on_show_image:
                    logger.debug(f"[show_image tool] Invoking on_show_image callback with '{resolved_path}', transition='{transition}', effect='{effect}'")
                    try:
                        self.on_show_image(
                            resolved_path,
                            transition=transition,
                            effect=effect,
                        )
                    except Exception as e:
                        logger.error(f"[show_image tool] Exception in on_show_image callback: {e}")
                elif not canvas_state_service:
                    logger.warning("[show_image tool] on_show_image callback is not set")
                self.currently_displayed_image_path = resolved_path
                self.currently_displayed_image_transition = transition
                self.currently_displayed_image_effect = effect
                res = f"Successfully displayed {resolved_path} to the user with transition '{transition}' and effect '{effect}'."
            else:
                logger.warning(f"[show_image tool] Image path or alias '{file_path}' could not be resolved.")
                res = f"Error: Image '{file_path}' not found."
            self._trigger_after_tool_call("show_image")
            return res
        except Exception as e:
            logger.error(f"[show_image tool] Exception occurred while showing image '{file_path}': {e}", exc_info=True)
            res = f"Error showing image: {e}"
            self._trigger_after_tool_call("show_image")
            return res

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
