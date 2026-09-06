"""Visual presentation state for a canvas theater."""

import time
from collections.abc import Callable
import os
from pathlib import Path
import re
from typing import Any, Optional, Tuple
from utils.image_utils import (
    extract_image_prompt,
    extract_image_metadata_description,
    extract_image_metadata_title,
)

class VisualState:
    def __init__(self) -> None:
        self.current_image_basename: str | None = None; self.shown_image_path: str | None = None
        self.shown_image_time = 0.0; self.shown_image_prompt = ""; self.shown_images_history: list[object] = []
        self.shown_image_transition = "crossfade"; self.shown_image_effect = "gleam3"
        self.shown_animation_frames: list[str] = []; self.shown_layered_animation: dict[str, object] | None = None
        self.shown_video_animation: dict[str, object] | None = None; self.image_revision = 0

    def initialize_starting_image(self, theater_id: str, theater_manager: Any, theater: Any) -> None:
        """Seed an empty canvas from the theater's configured reference image."""
        if self.shown_image_path or self._has_generated_image(theater):
            return
        try:
            from utils.config_loader import get_theater_config

            configured = get_theater_config(theater_id, theater_manager=theater_manager).get("starting_image", "")
        except Exception:
            return
        if not isinstance(configured, str) or not configured.strip():
            return
        image_path = self._find_starting_reference(theater, configured.strip())
        if image_path:
            self.show_image(str(image_path), url_for_path=theater.get_url_for_path)

    def _resolve_reference_fallback(self) -> Optional[Tuple[Path, str]]:
        """Fallback to first reference image (prioritizing cover-named images)."""
        if not self.theater_id:
            return None
        theater_ref_dir = self.theater.references_dir()
        if not theater_ref_dir.exists():
            return None
        ref_images = [f for f in theater_ref_dir.iterdir() if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
        if not ref_images:
            return None
        cover_candidates = [f for f in ref_images if "cover" in f.stem.lower()]
        chosen = cover_candidates[0] if cover_candidates else ref_images[0]
        prompt = extract_image_prompt(str(chosen)) or f"Mounted Reference: {chosen.stem}"
        return chosen, prompt

    def _resolve_prompt_for_file(self, file_path: Optional[str], fallback_prompt: str = "") -> str:
        """Robustly resolve human-readable prompt metadata for an image file."""
        if fallback_prompt:
            return fallback_prompt
        if not file_path or not os.path.exists(file_path):
            return ""
        # 1. Try embedded PNG tEXt or EXIF prompt
        prompt = extract_image_prompt(file_path)
        if prompt:
            return prompt
        # 2. Try embedded EXIF/PNG title or description fields
        title_meta = extract_image_metadata_title(file_path)
        if title_meta:
            return title_meta
        desc_meta = extract_image_metadata_description(file_path)
        if desc_meta:
            return desc_meta

        # 3. Check if image matches adventure cover
        path_obj = Path(file_path)
        meta = None
        if hasattr(self, "theater") and self.theater:
            try:
                meta_file = self.theater.directory() / "metadata.json"
                if meta_file.exists():
                    import json
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                meta = None
        if meta and meta.get("cover_image"):
            cover_name = Path(meta["cover_image"]).name.lower()
            if path_obj.name.lower() == cover_name:
                title = meta.get("title")
                return f"Adventure Cover: {title}" if title else f"Adventure Cover: {path_obj.stem}"

        # 4. Check for adjacent animation manifests (video.json, layered.json, triframe.json)
        parent_dir = path_obj.parent
        for manifest_name in ("video.json", "layered.json", "triframe.json"):
            candidate = parent_dir / manifest_name
            if candidate.is_file():
                try:
                    import json
                    data = json.loads(candidate.read_text(encoding="utf-8"))
                    anim_prompt = data.get("scene_prompt") or data.get("prompt")
                    if anim_prompt:
                        return str(anim_prompt).strip()
                except Exception:
                    pass

        # 5. Fallback based on filename stem
        stem_clean = path_obj.stem.replace("_", " ").replace("-", " ").title()
        if "reference" in str(path_obj).lower() or "ref" in path_obj.stem.lower():
            return f"Reference Visual: {stem_clean}"
        return f"Image: {stem_clean}"

    def _resolve_active_image(self) -> Tuple[Optional[str], Optional[str], float, str]:
        """Resolve current displayed image: explicit shown image, newest artifact, adventure cover, or mounted reference.

        Returns:
            Tuple of (image_url, file_path_str, timestamp, prompt_text)
        """
        # 1. Explicit shown image
        if self.shown_image_path:
            prompt = self._resolve_prompt_for_file(self.shown_image_path, getattr(self, "shown_image_prompt", ""))
            return self.get_url_for_path(self.shown_image_path), self.shown_image_path, self.shown_image_time, prompt

        # 2. Output directory latest generated image
        image_folder = str(self.theater.output_dir())
        if os.path.exists(image_folder):
            files = []
            for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
                files.extend(glob.glob(os.path.join(image_folder, ext)))
            if files:
                newest = max(files, key=os.path.getmtime)
                prompt = self._resolve_prompt_for_file(newest)
                return self.get_url_for_path(newest), newest, os.path.getmtime(newest), prompt

        # 3. Adventure cover from metadata.json
        cover = self._resolve_adventure_cover()
        if cover:
            cover_path, prompt = cover
            prompt = self._resolve_prompt_for_file(str(cover_path), prompt)
            return self.get_url_for_path(str(cover_path)), str(cover_path), 0.0, prompt

        # 4. Mounted reference fallback
        ref = self._resolve_reference_fallback()
        if ref:
            ref_path, prompt = ref
            prompt = self._resolve_prompt_for_file(str(ref_path), prompt)
            return self.get_url_for_path(str(ref_path)), str(ref_path), 0.0, prompt

        return None, None, 0.0, ""

    @staticmethod
    def _has_generated_image(theater: Any) -> bool:
        image_artifacts_dir = getattr(theater, "image_artifacts_dir", None)
        if not callable(image_artifacts_dir):
            return False
        image_dir = image_artifacts_dir()
        return image_dir.exists() and any(
            path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            for path in image_dir.rglob("*")
        )

    @staticmethod
    def _find_starting_reference(theater: Any, image_name: str) -> Path | None:
        references_dir = getattr(theater, "references_dir", None)
        if not callable(references_dir):
            return None
        reference_dir = references_dir()
        if not reference_dir.exists():
            return None
        normalized_name = re.sub(r"[^a-zA-Z0-9_-]", "_", image_name).lower()
        for image_path in reference_dir.rglob("*"):
            if not image_path.is_file() or image_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            normalized_stem = re.sub(r"[^a-zA-Z0-9_-]", "_", image_path.stem).lower()
            if image_name.lower() in {image_path.name.lower(), image_path.stem.lower()} or normalized_name == normalized_stem:
                return image_path
        return None

    def show_image(
        self, file_path: str, *, transition: str = "crossfade", effect: str = "gleam3",
        prompt: str = "", clear_animation: bool = True,
        animation: dict[str, object] | None = None, url_for_path: Callable[[str], str] | None = None,
    ) -> bool:
        """Record a visual presentation and return whether the scene changed."""
        transition, effect = transition or "crossfade", effect or "gleam3"
        changed = (file_path != self.shown_image_path or transition != self.shown_image_transition
                   or effect != self.shown_image_effect)
        if changed or not self.shown_image_time:
            self.shown_image_time = time.time()
        self.shown_image_path, self.shown_image_prompt = file_path, prompt
        self.shown_image_transition, self.shown_image_effect = transition, effect
        if clear_animation:
            self.shown_animation_frames = []
            self.shown_layered_animation = None
            self.shown_video_animation = None
            animation = None
        if changed:
            self.image_revision += 1
        if file_path:
            item: dict[str, object] = {
                "path": file_path, "url": url_for_path(file_path) if url_for_path else file_path,
                "prompt": prompt, "time": self.shown_image_time,
                "transition": transition, "effect": effect,
            }
            if animation:
                item["animation"] = animation
            last = self.shown_images_history[-1] if self.shown_images_history else None
            if not isinstance(last, dict) or last.get("path") != file_path or last.get("animation") != animation:
                self.shown_images_history.append(item)
                self.shown_images_history = self.shown_images_history[-100:]
            else:
                self.shown_images_history[-1] = item
        return changed

    def show_triframe(self, frame_paths: list[str], *, prompt: str = "",
                       url_for_path: Callable[[str], str] | None = None) -> bool:
        if len(frame_paths) != 3 or any(not path for path in frame_paths):
            raise ValueError("A tri-frame animation requires exactly three image paths.")
        self.shown_animation_frames = list(frame_paths)
        self.shown_layered_animation = self.shown_video_animation = None
        animation = {"type": "triframe", "frames": [url_for_path(path) if url_for_path else path for path in frame_paths],
                     "frame_paths": list(frame_paths), "frame_duration_ms": 1400,
                     "crossfade_duration_ms": 500, "scene_prompt": prompt}
        return self.show_image(frame_paths[0], transition="crossfade", effect="none", prompt=prompt,
                               clear_animation=False, animation=animation, url_for_path=url_for_path)

    def show_layered_animation(self, manifest: dict[str, object], *,
                               url_for_path: Callable[[str], str] | None = None) -> bool:
        layers = manifest.get("layers") if isinstance(manifest, dict) else None
        if not isinstance(layers, list) or len(layers) < 2:
            raise ValueError("A layered animation requires at least two layers.")
        base_path = str(manifest.get("base_image") or layers[0].get("path") or "")
        if not base_path:
            raise ValueError("A layered animation requires a base image path.")
        prompt = str(manifest.get("scene_prompt") or manifest.get("prompt") or "")
        self.shown_animation_frames = []; self.shown_video_animation = None
        self.shown_layered_animation = {"id": manifest.get("id"), "scene_prompt": prompt, "layers": [
            {"name": item.get("name", f"layer_{index + 1}"), "description": item.get("description", ""),
             "effect": item.get("effect", "none"), "order": item.get("order", index),
             "url": url_for_path(str(item["path"])) if url_for_path else str(item["path"])}
            for index, item in enumerate(layers) if isinstance(item, dict) and item.get("path")
        ]}
        return self.show_image(base_path, transition="crossfade", effect="none", prompt=prompt,
                               clear_animation=False, animation={"type": "layered", **self.shown_layered_animation},
                               url_for_path=url_for_path)

    def show_video_animation(self, manifest: dict[str, object], *,
                             url_for_path: Callable[[str], str] | None = None) -> bool:
        video_path = str(manifest.get("video_path") or manifest.get("path") or "")
        video_url = manifest.get("video_url") or (url_for_path(video_path) if video_path and url_for_path else video_path)
        if not video_url:
            raise ValueError("A video animation requires a video URL or valid video path.")
        poster_path = str(manifest.get("poster_image") or "")
        prompt = str(manifest.get("scene_prompt") or manifest.get("prompt") or "")
        local_url = url_for_path(video_path) if video_path and url_for_path else video_path
        self.shown_animation_frames = []; self.shown_layered_animation = None
        self.shown_video_animation = {"id": manifest.get("id"), "scene_prompt": prompt, "video_url": video_url,
            "local_video_url": local_url, "fallback_url": local_url,
            "poster_url": url_for_path(poster_path) if poster_path and url_for_path else poster_path or None,
            "video_duration_seconds": manifest.get("video_duration_seconds", manifest.get("duration_seconds", manifest.get("duration", 5))),
            "loop": manifest.get("loop", True), "muted": manifest.get("muted", True)}
        display_target = poster_path or video_path or str(video_url)
        return self.show_image(display_target, transition="crossfade", effect="none", prompt=prompt,
                               clear_animation=False, animation={"type": "video", **self.shown_video_animation},
                               url_for_path=url_for_path)

    def load(self, data: dict[str, object]) -> None:
        for name in ("current_image_basename", "shown_image_path", "shown_image_prompt", "shown_image_transition", "shown_image_effect"):
            value = data.get(name)
            if isinstance(value, str): setattr(self, name, value)
        history = data.get("shown_images_history", [])
        self.shown_images_history = list(history) if isinstance(history, list) else []
        frames = data.get("shown_animation_frames", [])
        self.shown_animation_frames = [frame for frame in frames if isinstance(frame, str)] if isinstance(frames, list) else []
        self.shown_layered_animation = data.get("shown_layered_animation") if isinstance(data.get("shown_layered_animation"), dict) else None
        self.shown_video_animation = data.get("shown_video_animation") if isinstance(data.get("shown_video_animation"), dict) else None

    def serialize(self) -> dict[str, object]:
        return {"current_image_basename": self.current_image_basename, "shown_image_path": self.shown_image_path,
                "shown_image_prompt": self.shown_image_prompt, "shown_images_history": self.shown_images_history,
                "shown_image_transition": self.shown_image_transition, "shown_image_effect": self.shown_image_effect,
                "shown_animation_frames": self.shown_animation_frames, "shown_layered_animation": self.shown_layered_animation,
                "shown_video_animation": self.shown_video_animation}

    def payload(self, theater: object) -> dict[str, object]:
        image_url = None
        if self.shown_image_path:
            image_url = self.shown_image_path if self.shown_image_path.startswith(("http://", "https://", "/theaters/")) else theater.get_url_for_path(self.shown_image_path)
        result = {"latest": image_url, "time": self.shown_image_time, "prompt": self.shown_image_prompt,
                  "transition": self.shown_image_transition, "effect": self.shown_image_effect, "history": self.shown_images_history}
        if self.shown_animation_frames:
            result["animation"] = {
                "type": "triframe",
                "frame_paths": self.shown_animation_frames,
                "frames": [
                    path if path.startswith(("http://", "https://", "/theaters/")) else theater.get_url_for_path(path)
                    for path in self.shown_animation_frames
                ],
                "frame_duration_ms": 1400,
                "crossfade_duration_ms": 500,
                "scene_prompt": self.shown_image_prompt,
            }
        elif self.shown_layered_animation: result["animation"] = {"type": "layered", **self.shown_layered_animation}
        elif self.shown_video_animation: result["animation"] = {"type": "video", **self.shown_video_animation}
        return result
