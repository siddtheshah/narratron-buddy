from __future__ import annotations

import glob
import logging
import os
from pathlib import Path
import re
import time
from collections.abc import Callable
from typing import Any, Optional, Tuple

from components.theater_manager import Theater
from utils.image_utils import (
    extract_image_prompt,
    extract_image_metadata_description,
    extract_image_metadata_title,
)

logger = logging.getLogger("components.canvas.visual_state")

class VisualState:
    def __init__(self, theater: Theater, theater_id: Optional[str] = None) -> None:
        self.theater: Theater = theater
        th_id = theater_id
        if not th_id:
            try:
                if isinstance(theater.theater_id, str):
                    th_id = theater.theater_id
            except AttributeError:
                th_id = None
        self.theater_id: str | None = th_id
        self.current_image_basename: str | None = None
        self.shown_image_path: str | None = None
        self.shown_image_time = 0.0
        self.shown_image_prompt = ""
        self.shown_images_history: list[dict[str, object]] = []
        self.shown_image_transition = "crossfade"
        self.shown_image_effect = "gleam3"
        self.shown_animation_frames: list[str] = []
        self.shown_layered_animation: dict[str, object] | None = None
        self.shown_video_animation: dict[str, object] | None = None
        self.image_revision = 0

    def get_url_for_path(self, file_path: str, theater: Optional[Theater] = None) -> str:
        """Resolve a public or client-accessible URL for an image path."""
        if not file_path:
            return ""
        if file_path.startswith(("http://", "https://", "/theaters/")):
            return file_path
        th = theater or self.theater
        try:
            return th.get_url_for_path(file_path)
        except Exception:
            return file_path

    def initialize_starting_image(self, theater_id: str | None = None, theater_manager: Any = None, theater: Optional[Theater] = None) -> None:
        """Seed an empty canvas from the theater's configured reference image."""
        th = theater or self.theater
        th_id = theater_id or self.theater_id or ""
        if self.shown_image_path or self._has_generated_image(th):
            return
        try:
            from utils.config_loader import get_theater_config

            configured = get_theater_config(th_id, theater_manager=theater_manager).get("starting_image", "")
        except Exception:
            return
        if not isinstance(configured, str) or not configured.strip():
            return
        image_path = self._find_starting_reference(th, configured.strip())
        if image_path:
            self.show_image(str(image_path), url_for_path=th.get_url_for_path)

    def _resolve_adventure_cover(self, theater: Optional[Theater] = None) -> Optional[Tuple[Path, str]]:
        """Look up adventure cover image from metadata.json if present."""
        th = theater or self.theater
        try:
            th_id = self.theater_id or th.theater_id
            if not th_id or not isinstance(th_id, str):
                return None
            theater_dir = th.directory()
            try:
                theater_ref_dir = th.references_dir()
            except Exception:
                theater_ref_dir = None
        except Exception:
            return None
        if not isinstance(theater_dir, (str, Path)):
            return None
        theater_dir = Path(theater_dir)
        if not theater_dir.exists():
            return None
        metadata_file = theater_dir / "metadata.json"
        if not metadata_file.exists():
            return None

        try:
            import json
            meta = json.loads(metadata_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to read metadata.json for theater {th_id}: {e}")
            return None

        cover_image_setting = meta.get("cover_image")
        cover_file: Path | None = None
        if cover_image_setting and isinstance(cover_image_setting, str):
            clean_cover = cover_image_setting
            for prefix in ("references/", "reference_library/"):
                if clean_cover.startswith(prefix):
                    clean_cover = clean_cover[len(prefix):]

            candidates: list[Path] = []
            if theater_ref_dir and isinstance(theater_ref_dir, (str, Path)):
                ref_path = Path(theater_ref_dir)
                candidates.extend([
                    ref_path / cover_image_setting,
                    ref_path / clean_cover,
                    ref_path / Path(cover_image_setting).name,
                ])
            candidates.extend([
                theater_dir / cover_image_setting,
                theater_dir / clean_cover,
            ])
            for cand in candidates:
                if cand.exists() and cand.is_file():
                    cover_file = cand
                    break

            if not cover_file and theater_ref_dir and isinstance(theater_ref_dir, (str, Path)):
                ref_path = Path(theater_ref_dir)
                if ref_path.exists():
                    match_name = Path(cover_image_setting).name.lower()
                    for f in ref_path.rglob("*"):
                        if f.is_file() and f.name.lower() == match_name:
                            cover_file = f
                            break

        if cover_file:
            title = meta.get("title")
            prompt = extract_image_prompt(str(cover_file)) or (
                f"Adventure Cover: {title}" if title else f"Mounted Reference: {cover_file.stem}"
            )
            return cover_file, prompt
        return None

    def _resolve_reference_fallback(self, theater: Optional[Theater] = None) -> Optional[Tuple[Path, str]]:
        """Fallback to first reference image (prioritizing cover-named images)."""
        th = theater or self.theater
        try:
            th_id = self.theater_id or th.theater_id
            if not th_id or not isinstance(th_id, str):
                return None
            theater_ref_dir = th.references_dir()
            if not isinstance(theater_ref_dir, (str, Path)):
                return None
            theater_ref_dir = Path(theater_ref_dir)
            if not theater_ref_dir.exists():
                return None
            ref_images = [f for f in theater_ref_dir.iterdir() if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
            if not ref_images:
                return None
            cover_candidates = [f for f in ref_images if "cover" in f.stem.lower()]
            chosen = cover_candidates[0] if cover_candidates else ref_images[0]
            prompt = extract_image_prompt(str(chosen)) or f"Mounted Reference: {chosen.stem}"
            return chosen, prompt
        except Exception as e:
            logger.warning("Failed to resolve reference fallback: %s", e)
            return None

    def _resolve_prompt_for_file(
        self, file_path: Optional[str], fallback_prompt: str = "", theater: Optional[Theater] = None
    ) -> str:
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
        th = theater or self.theater
        try:
            td = th.directory()
            if isinstance(td, (str, Path)):
                meta_file = Path(td) / "metadata.json"
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

    def _resolve_active_image(self, theater: Optional[Theater] = None) -> Tuple[Optional[str], Optional[str], float, str]:
        """Resolve current displayed image: explicit shown image, newest artifact, adventure cover, or mounted reference.

        Returns:
            Tuple of (image_url, file_path_str, timestamp, prompt_text)
        """
        th = theater or self.theater

        # 1. Explicit shown image
        if self.shown_image_path:
            prompt = self._resolve_prompt_for_file(
                self.shown_image_path,
                self.shown_image_prompt,
                theater=th,
            )
            time_val = self.shown_image_time
            if not time_val and os.path.exists(self.shown_image_path):
                try:
                    time_val = os.path.getmtime(self.shown_image_path)
                except Exception:
                    pass
            return self.get_url_for_path(self.shown_image_path, th), self.shown_image_path, time_val, prompt

        # 2. Output directory latest generated image
        try:
            out = th.output_dir()
            if isinstance(out, (str, Path)):
                image_folder = str(out)
                if os.path.exists(image_folder):
                    files: list[str] = []
                    for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
                        files.extend(glob.glob(os.path.join(image_folder, ext)))
                    if files:
                        newest = max(files, key=os.path.getmtime)
                        prompt = self._resolve_prompt_for_file(newest, theater=th)
                        return self.get_url_for_path(newest, th), newest, os.path.getmtime(newest), prompt
        except Exception as e:
            logger.warning("Failed to resolve latest generated image from output dir: %s", e)

        # 3. Adventure cover from metadata.json
        cover = self._resolve_adventure_cover(th)
        if cover:
            cover_path, prompt = cover
            prompt = self._resolve_prompt_for_file(str(cover_path), prompt, theater=th)
            return self.get_url_for_path(str(cover_path), th), str(cover_path), 0.0, prompt

        # 4. Mounted reference fallback
        ref = self._resolve_reference_fallback(th)
        if ref:
            ref_path, prompt = ref
            prompt = self._resolve_prompt_for_file(str(ref_path), prompt, theater=th)
            return self.get_url_for_path(str(ref_path), th), str(ref_path), 0.0, prompt

        return None, None, 0.0, ""

    @staticmethod
    def _has_generated_image(theater: Theater) -> bool:
        try:
            image_dir = theater.image_artifacts_dir()
            return image_dir.exists() and any(
                path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                for path in image_dir.rglob("*")
            )
        except Exception:
            return False

    @staticmethod
    def _find_starting_reference(theater: Theater, image_name: str) -> Path | None:
        try:
            reference_dir = theater.references_dir()
            if not reference_dir.exists():
                return None
        except Exception:
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
        if isinstance(data.get("shown_image_time"), (int, float)):
            self.shown_image_time = float(data["shown_image_time"])
        elif isinstance(data.get("time"), (int, float)):
            self.shown_image_time = float(data["time"])
        history = data.get("shown_images_history", [])
        self.shown_images_history = list(history) if isinstance(history, list) else []
        frames = data.get("shown_animation_frames", [])
        self.shown_animation_frames = [frame for frame in frames if isinstance(frame, str)] if isinstance(frames, list) else []
        self.shown_layered_animation = data.get("shown_layered_animation") if isinstance(data.get("shown_layered_animation"), dict) else None
        self.shown_video_animation = data.get("shown_video_animation") if isinstance(data.get("shown_video_animation"), dict) else None

    def serialize(self) -> dict[str, object]:
        return {"current_image_basename": self.current_image_basename, "shown_image_path": self.shown_image_path,
                "shown_image_prompt": self.shown_image_prompt, "shown_images_history": list(self.shown_images_history),
                "shown_image_transition": self.shown_image_transition, "shown_image_effect": self.shown_image_effect,
                "shown_animation_frames": list(self.shown_animation_frames), "shown_layered_animation": self.shown_layered_animation,
                "shown_video_animation": self.shown_video_animation}

    def payload(self, theater: Optional[Theater] = None) -> dict[str, object]:
        th = theater or self.theater
        if th is not None:
            self.theater = th
            try:
                th_id = th.theater_id
                if th_id and isinstance(th_id, str) and not self.theater_id:
                    self.theater_id = th_id
            except AttributeError:
                pass

        image_url, selected_file, selected_time, prompt_text = self._resolve_active_image(th)

        if selected_file:
            self.current_image_basename = os.path.basename(selected_file)

        transition = self.shown_image_transition or "crossfade"
        effect = self.shown_image_effect or "gleam3"

        formatted_history: list[dict[str, object]] = []
        for h in self.shown_images_history:
            if isinstance(h, dict):
                h_copy = dict(h)
                h_path = str(h_copy.get("path") or "")
                h_prompt = str(h_copy.get("prompt") or "")
                if h_path:
                    h_copy["prompt"] = self._resolve_prompt_for_file(h_path, h_prompt, theater=th)
                if h_copy.get("animation") and isinstance(h_copy["animation"], dict):
                    anim = dict(h_copy["animation"])
                    if anim.get("type") == "triframe" and isinstance(anim.get("frame_paths"), list):
                        anim["frames"] = [self.get_url_for_path(str(p), th) for p in anim["frame_paths"]]
                    h_copy["animation"] = anim
                formatted_history.append(h_copy)
            elif isinstance(h, str):
                formatted_history.append({
                    "path": h,
                    "url": self.get_url_for_path(h, th),
                    "prompt": self._resolve_prompt_for_file(h, theater=th),
                    "time": 0.0,
                    "transition": transition,
                    "effect": effect,
                })

        anim_info: dict[str, object] | None = None
        if self.shown_animation_frames:
            anim_info = {
                "type": "triframe",
                "frame_paths": list(self.shown_animation_frames),
                "frames": [
                    self.get_url_for_path(path, th)
                    for path in self.shown_animation_frames
                ],
                "frame_duration_ms": 1400,
                "crossfade_duration_ms": 500,
                "scene_prompt": self.shown_image_prompt or prompt_text,
            }
        elif self.shown_layered_animation:
            anim_info = {"type": "layered", **self.shown_layered_animation}
        elif self.shown_video_animation:
            anim_info = {"type": "video", **self.shown_video_animation}

        if selected_file and not formatted_history:
            item: dict[str, object] = {
                "path": selected_file,
                "url": image_url if image_url is not None else selected_file,
                "prompt": prompt_text,
                "time": selected_time,
                "transition": transition,
                "effect": effect,
            }
            if anim_info:
                item["animation"] = anim_info
            formatted_history.append(item)

        result: dict[str, object] = {
            "latest": image_url,
            "time": selected_time,
            "prompt": prompt_text,
            "transition": transition,
            "effect": effect,
            "history": formatted_history,
        }
        if anim_info:
            result["animation"] = anim_info
        return result
