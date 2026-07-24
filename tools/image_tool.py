import base64
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import time
from io import BytesIO
from typing import Dict, List, Optional, Union

from google import genai
from google.genai import types
from PIL import Image

from utils.image_utils import (
    embed_image_metadata,
    extract_image_metadata_description,
    extract_image_prompt,
    resolve_image_path,
)

logger = logging.getLogger(__name__)

class ImageTools:
    _client_cache = None
    _ref_library_cache: Dict[str, dict] = {}
    _ref_library_dir_cached: Optional[str] = None

    def __init__(self, config: dict, session_id: str):
        root_dir = Path(__file__).parent.parent.resolve()
        self.active_session_id: str = session_id
        
        self.output_dir = str((root_dir / "sessions" / self.active_session_id / "output" / "artifacts" / "images").resolve())
        os.makedirs(self.output_dir, exist_ok=True)
        
        self.reference_library_dir = str((root_dir / "sessions" / self.active_session_id / "references").resolve())
        os.makedirs(self.reference_library_dir, exist_ok=True)
        
        # Reuse shared genai Client instance across session re-initializations
        if ImageTools._client_cache is None:
            project_id = config.get("gcloud", {}).get("project_id", os.getenv("GOOGLE_CLOUD_PROJECT"))
            location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
            ImageTools._client_cache = genai.Client(vertexai=True, project=project_id, location=location)
        
        self.client = ImageTools._client_cache

        self.on_show_image = None
        self.last_create_time = 0.0
        self.last_show_time = 0.0
        self.cooldown_duration = float(config.get("image_generation", {}).get("cooldown_duration", 60.0))

        # In-memory mapping of custom image names/aliases to file paths
        self.image_aliases: Dict[str, str] = {}
        
        # Reuse cached reference library manifest if directory hasn't changed
        if ImageTools._ref_library_dir_cached == self.reference_library_dir and ImageTools._ref_library_cache:
            self.reference_library_manifest = ImageTools._ref_library_cache
        else:
            self.reference_library_manifest = {}
            self._load_reference_library()
            ImageTools._ref_library_cache = self.reference_library_manifest
            ImageTools._ref_library_dir_cached = self.reference_library_dir

    def get_effective_output_dir(self) -> str:
        """Return active session output directory."""
        return self.output_dir

    def _load_reference_library(self):
        """Scans the reference library folder once at startup and builds a read-only manifest."""
        try:
            if os.path.exists(self.reference_library_dir):
                for filename in os.listdir(self.reference_library_dir):
                    if filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        filepath = os.path.join(self.reference_library_dir, filename)
                        stem = Path(filename).stem
                        clean_stem = re.sub(r'[^a-zA-Z0-9_-]', '_', stem)
                        
                        metadata_desc = extract_image_metadata_description(filepath)

                        entry = {
                            "name": stem,
                            "alias": clean_stem,
                            "path": filepath,
                            "description": metadata_desc or f"Reference library image {filename}"
                        }
                        self.reference_library_manifest[stem] = entry
                        self.reference_library_manifest[clean_stem] = entry
                        self.reference_library_manifest[stem.lower()] = entry
                        self.reference_library_manifest[clean_stem.lower()] = entry
            unique_count = len(set(item['path'] for item in self.reference_library_manifest.values())) if self.reference_library_manifest else 0
            logger.info(f"[ImageTools] Loaded {unique_count} reference images into read-only library.")
        except Exception as e:
            logger.warning(f"[ImageTools] Failed to load reference library: {e}")

    def list_reference_library(self) -> list[dict]:
        """List all available pre-loaded reference images in the reference library.

        Returns:
            A list of dictionaries with image names, file paths, and metadata descriptions.
        """
        seen_paths = set()
        results = []
        for item in self.reference_library_manifest.values():
            if item["path"] not in seen_paths:
                seen_paths.add(item["path"])
                results.append({
                    "name": item["name"],
                    "alias": item["alias"],
                    "path": item["path"],
                    "description": item["description"]
                })
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

        # Check reference library manifest
        if path_str in self.reference_library_manifest:
            return self.reference_library_manifest[path_str]["path"]
        if path_str.lower() in self.reference_library_manifest:
            return self.reference_library_manifest[path_str.lower()]["path"]
        if clean_input in self.reference_library_manifest:
            return self.reference_library_manifest[clean_input]["path"]
        if clean_input.lower() in self.reference_library_manifest:
            return self.reference_library_manifest[clean_input.lower()]["path"]

        # General path resolution
        return resolve_image_path(path_str, [self.get_effective_output_dir(), self.output_dir, self.reference_library_dir])

    def create_image(
        self,
        image_prompt: str,
        metadata_description: str,
        image_name: Optional[str] = None,
        reference_images: Union[list[str], str, None] = None
    ) -> str:
        """Generates an image from a prompt, supporting custom image naming and reference image adaptation.

        Args:
            image_prompt: The prompt describing the image to generate.
            metadata_description: A description to embed as metadata in the image.
            image_name: Optional friendly name/alias for the generated image (e.g. 'hero_portrait', 'oasis_v1').
            reference_images: Optional reference image name(s) or file path(s) to adapt style or visual context.

        Returns:
            A string indicating the file path of the saved generated image, or an error message.
        """
        try:
            now = time.time()
            elapsed = now - self.last_create_time
            if elapsed < self.cooldown_duration:
                remaining = int(self.cooldown_duration - elapsed)
                return f"Error: create_image is on cooldown. Please wait {remaining} more seconds before generating another image."

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
                        logger.warning(f"[create_image tool] Reference image '{ref}' not found.")

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
                
                logger.info(f"[create_image tool] Adapted prompt with {len(resolved_refs)} reference images by bytes.")

            prompt_parts.append(image_prompt)

            logger.info(f"[create_image tool] Generating image from prompt: {image_prompt[:100]}...")
            
            response = self.client.models.generate_content(
                model="gemini-3.1-flash-image",
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
                
                out_folder = self.get_effective_output_dir()
                filepath = os.path.join(out_folder, filename)
                 
                exif = image.getexif()
                embed_image_metadata(exif, image_prompt, metadata_description)
                 
                image.save(filepath, "JPEG", exif=exif, quality=95)
                saved_paths.append(filepath)
                
                # Register alias
                if image_name:
                    clean_name = re.sub(r'[^a-zA-Z0-9_-]', '_', image_name)
                    self.image_aliases[image_name] = filepath
                    self.image_aliases[clean_name] = filepath
                    self.image_aliases[image_name.lower()] = filepath
                    self.image_aliases[clean_name.lower()] = filepath
                
                logger.info(f"[create_image tool] Saved image to {filepath} (Name alias: {image_name})")
            
            if not saved_paths:
                details = ""
                if text_feedback:
                    details = f" Details: {' '.join(text_feedback)}"
                elif getattr(response, "candidates", None) and response.candidates:
                    cand = response.candidates[0]
                    finish_reason = cand.get("finish_reason") if isinstance(cand, dict) else getattr(cand, "finish_reason", None)
                    if finish_reason:
                        details = f" Finish reason: {finish_reason}"
                elif getattr(response, "prompt_feedback", None):
                    pf = getattr(response, "prompt_feedback")
                    block_reason = pf.get("block_reason") if isinstance(pf, dict) else getattr(pf, "block_reason", None)
                    if block_reason:
                        details = f" Prompt block reason: {block_reason}"
                return f"Failed to generate image: Model didn't return binary image data.{details}"

            self.last_create_time = time.time()
            if self.on_show_image:
                self.on_show_image(saved_paths[0])
            
            name_msg = f" with alias '{image_name}'" if image_name else ""
            ref_msg = f" using references {[r[0] for r in resolved_refs]}" if resolved_refs else ""
            return f"Successfully generated and displayed image{name_msg}{ref_msg} at {saved_paths[0]}"
        except Exception as e:
            error_msg = f"Error generating image: {e}"
            logger.error(f"[create_image tool] {error_msg}")
            return error_msg

    def show_image(self, file_path: str) -> str:
        """Shows an image from a file path or friendly name to the user.

        Args:
            file_path: The file path or friendly name/alias of the image to show.

        Returns:
            A status message indicating success or failure.
        """
        try:
            logger.debug(f"[show_image tool] Called with file_path='{file_path}'")
            now = time.time()
            elapsed = now - self.last_show_time
            if elapsed < self.cooldown_duration:
                remaining = int(self.cooldown_duration - elapsed)
                logger.warning(
                    f"[show_image tool] On cooldown. Elapsed: {elapsed:.2f}s, "
                    f"Cooldown: {self.cooldown_duration}s, Remaining: {remaining}s"
                )
                return f"Error: show_image is on cooldown. Please wait {remaining} more seconds before displaying another image."

            resolved_path = self._find_image_path(file_path)
            logger.info(f"[show_image tool] Showing image from '{file_path}' (resolved: '{resolved_path}')")
            if resolved_path:
                if self.on_show_image:
                    logger.debug(f"[show_image tool] Invoking on_show_image callback with '{resolved_path}'")
                    self.on_show_image(resolved_path)
                else:
                    logger.warning("[show_image tool] on_show_image callback is not set")
                self.last_show_time = time.time()
                return f"Successfully displayed {resolved_path} to the user."
            else:
                logger.warning(f"[show_image tool] Image path or alias '{file_path}' could not be resolved.")
                return f"Error: Image '{file_path}' not found."
        except Exception as e:
            logger.error(f"[show_image tool] Exception occurred while showing image '{file_path}': {e}", exc_info=True)
            return f"Error showing image: {e}"

    def browse_images(self) -> list[str]:
        """Browse all available images, including preloaded reference library assets and generated outputs.

        Returns:
            A list of file paths to all available images.
        """
        try:
            images = []
            seen = set()
            
            # Include items from preloaded reference library manifest first
            for item in self.reference_library_manifest.values():
                full_p = item["path"]
                if full_p not in seen and os.path.exists(full_p):
                    seen.add(full_p)
                    images.append(full_p)

            search_dirs = [
                self.get_effective_output_dir(),
                self.output_dir,
                self.reference_library_dir,
                str(Path(__file__).parent.parent / "testing" / "testdata" / "images"),
            ]
            for d in search_dirs:
                if os.path.exists(d):
                    for filename in os.listdir(d):
                        if filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                            full_p = os.path.join(d, filename)
                            if full_p not in seen:
                                seen.add(full_p)
                                images.append(full_p)
            return images
        except Exception as e:
            return [f"Error browsing images: {e}"]

    def search_image_by_metadata(self, metadata_query: str) -> list[str]:
        """Search for images that match a given metadata description across generated images and the reference library.

        Args:
            metadata_query: A string to search for in image metadata descriptions, names, or EXIF data.

        Returns:
            A list of file paths to images matching the query.
        """
        try:
            matches = []
            seen = set()
            query_lower = metadata_query.lower()

            # Search in-memory reference library manifest
            for item in self.reference_library_manifest.values():
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
                self.get_effective_output_dir(),
                self.output_dir,
                self.reference_library_dir,
                str(Path(__file__).parent.parent / "testing" / "testdata" / "images"),
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
            return matches
        except Exception as e:
            return [f"Error searching images: {e}"]
