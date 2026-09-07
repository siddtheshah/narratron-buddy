from __future__ import annotations

import glob
import logging
import os
from pathlib import Path
import re
import threading
import time
from collections.abc import Callable
from typing import Any, Optional, Tuple, Union

from components.theater_manager import Theater
from utils.image_utils import (
    compress_image_to_webp,
    extract_image_prompt,
    extract_image_metadata_description,
    extract_image_metadata_title,
)

logger = logging.getLogger("components.canvas.visual_state")

PRIORITY_SHOW = 1
PRIORITY_CREATE = 2

class VisualState:
    PRIORITY_SHOW = PRIORITY_SHOW
    PRIORITY_CREATE = PRIORITY_CREATE

    def __init__(
        self,
        theater: Theater,
        notify_changed_fn: Optional[Callable[..., None]] = None,
        on_visual_changed_fn: Optional[Callable[[bool], None]] = None,
    ) -> None:
        self.theater = theater
        self.theater_id: str | None = theater.theater_id
        self.theater_config: dict[str, Any] = theater.config() or {}

        self.notify_changed_fn = notify_changed_fn
        self.on_visual_changed_fn = on_visual_changed_fn
        self.on_show_image: Optional[Callable[..., None]] = None

        # Cycle configuration and state
        img_cfg = self.theater_config.get("image_generation", self.theater_config) if isinstance(self.theater_config, dict) else {}
        self.cooldown_duration: float = float(img_cfg.get("cooldown_duration", 60.0))
        self.cycle_length: float = float(img_cfg.get("cycle_length", self.cooldown_duration))
        self._cycle_lock = threading.RLock()
        self._cycle_timer: Optional[threading.Timer] = None
        self._cycle_active: bool = False
        self.current_cycle_visual: Optional[dict] = None
        self.next_cycle_image: Optional[dict] = None

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
        return th.get_url_for_path(file_path)

    def has_active_animation(self) -> bool:
        """Check if an animation is currently active on the canvas."""
        return bool(
            self.shown_video_animation
            or self.shown_layered_animation
            or self.shown_animation_frames
        )

    def has_active_visual(self) -> bool:
        """Check if any visual (image or animation) is currently active on the canvas."""
        return bool(
            self.current_cycle_visual
            or self.shown_image_path
            or self.has_active_animation()
        )

    def _ensure_webp_for_display(self, file_path: str) -> str:
        """Ensures a compressed WebP version of the image exists in output_dir for frontend display."""
        if not file_path:
            return file_path
        if file_path.lower().endswith(".webp"):
            return file_path

        img_dir = self.theater.image_artifacts_dir()
        if isinstance(img_dir, (str, Path)):
            out_dir = str(img_dir)
        else:
            out_dir = os.path.dirname(file_path) or "."

        try:
            os.makedirs(out_dir, exist_ok=True)
            stem = Path(file_path).stem
            webp_filename = f"{stem}.webp"
            webp_path = os.path.join(out_dir, webp_filename)

            if os.path.exists(webp_path) and os.path.exists(file_path):
                if os.path.getmtime(webp_path) >= os.path.getmtime(file_path):
                    return webp_path

            compressed = compress_image_to_webp(file_path, output_path=webp_path, quality=80)
            return compressed if compressed and os.path.exists(compressed) else file_path
        except Exception as exc:
            logger.warning("[VisualState] Failed to compress image to webp: %s", exc)
            return file_path

    def update_image(
        self,
        path: str,
        *,
        display_path: Optional[str] = None,
        transition: str = "crossfade",
        effect: str = "gleam3",
        prompt: str = "",
        priority: int = PRIORITY_SHOW,
        source: str = "show_image",
        url_for_path: Optional[Callable[[str], str]] = None,
    ) -> dict[str, Any]:
        """Request an image update on the canvas, paced by the visual cycle.

        Args:
            path: Original file path or reference image path.
            display_path: Optional WebP or frontend-optimized display path.
            transition: Visual transition ('crossfade', 'fade', 'none').
            effect: Canvas animation effect ('gleam3', 'dream', etc.).
            prompt: Text prompt or description of the image.
            priority: PRIORITY_SHOW (1) or PRIORITY_CREATE (2).
            source: Caller identifier ('show_image', 'create_image').
            url_for_path: Optional path-to-URL resolver.

        Returns:
            dict with 'status' ('displayed' | 'queued' | 'blocked'), 'resource', and 'message'.
        """
        item = {
            "type": "image",
            "path": path,
            "display_path": display_path or path,
            "transition": transition or "crossfade",
            "effect": effect or "gleam3",
            "prompt": prompt,
            "priority": priority,
            "source": source,
            "url_for_path": url_for_path,
        }
        return self._enqueue_or_display(item)

    def update_animation(
        self,
        animation_type: str,
        data: Union[dict[str, Any], list[str]],
        *,
        id: str = "",
        prompt: str = "",
        priority: int = PRIORITY_SHOW,
        source: str = "play_animation",
        force_immediate: bool = False,
        url_for_path: Optional[Callable[[str], str]] = None,
    ) -> dict[str, Any]:
        """Request an animation update on the canvas.

        Args:
            animation_type: 'video', 'layered', or 'triframe'.
            data: Manifest dict (for video/layered) or frame paths list (for triframe).
            id: Animation identifier.
            prompt: Scene prompt.
            priority: Priority level.
            source: Caller identifier ('play_animation', 'create_animation').
            force_immediate: Whether animation should bypass pacing and play immediately.
            url_for_path: Optional path-to-URL resolver.

        Returns:
            dict with 'status' ('displayed' | 'queued' | 'blocked'), 'resource', and 'message'.
        """
        item: dict[str, Any] = {
            "type": animation_type,
            "id": id,
            "prompt": prompt,
            "priority": priority,
            "source": source,
            "force_immediate": force_immediate,
            "url_for_path": url_for_path,
        }
        if animation_type == "triframe" and isinstance(data, list):
            item["frame_paths"] = data
            if data:
                item["path"] = data[0]
        elif isinstance(data, dict):
            item["manifest"] = data
            if animation_type == "video":
                item["path"] = str(data.get("video_path") or data.get("poster_image") or data.get("video_url") or "")
            elif animation_type == "layered":
                item["path"] = str(data.get("base_image") or "")

        return self._enqueue_or_display(item)

    def update_visual(
        self,
        resource: Optional[Union[dict[str, Any], str]] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generic visual update dispatch accepting dict or keyword arguments."""
        if isinstance(resource, str):
            kwargs.setdefault("path", resource)
        elif isinstance(resource, dict):
            kwargs = {**resource, **kwargs}

        res_type = kwargs.pop("type", kwargs.pop("resource_type", "image"))
        if res_type == "image":
            path = kwargs.pop("path", "")
            return self.update_image(path, **kwargs)
        else:
            data = kwargs.pop("manifest", kwargs.pop("frame_paths", kwargs.pop("data", {})))
            return self.update_animation(res_type, data, **kwargs)

    def _enqueue_or_display(self, item: dict[str, Any]) -> dict[str, Any]:
        """Internal pacing logic for scheduling or immediately displaying a visual resource."""
        force_immediate = bool(item.get("force_immediate", False))

        with self._cycle_lock:
            has_active = self.has_active_visual()
            is_cold_start = not has_active

            display_immediately = force_immediate or is_cold_start

            if display_immediately:
                self.current_cycle_visual = item
                self.next_cycle_image = None
                self._apply_visual(item)
                self._schedule_next_cycle_tick()
                target_desc = item.get("raw_path") or item.get("path") or item.get("id") or item.get("type")
                return {
                    "status": "displayed",
                    "resource": item,
                    "message": f"Successfully displayed {target_desc} with transition '{item.get('transition', 'crossfade')}' and effect '{item.get('effect', 'gleam3')}'."
                }

            item_priority = int(item.get("priority", self.PRIORITY_SHOW))
            if (
                self.next_cycle_image
                and int(self.next_cycle_image.get("priority", 0)) >= self.PRIORITY_CREATE
                and item_priority < self.PRIORITY_CREATE
            ):
                target_desc = item.get("raw_path") or item.get("path") or item.get("id") or item.get("type")
                logger.info(
                    "[VisualState] update_visual rejected for '%s': higher-priority visual already queued.",
                    target_desc,
                )
                return {
                    "status": "blocked",
                    "resource": item,
                    "message": "Visual resource was not queued because a higher-priority resource already has priority for the next cycle."
                }

            self.next_cycle_image = item
            if not self._cycle_active and self.cycle_length > 0:
                self._schedule_next_cycle_tick()

            target_desc = item.get("raw_path") or item.get("path") or item.get("id") or item.get("type")
            return {
                "status": "queued",
                "resource": item,
                "message": f"Visual resource '{target_desc}' queued for the next cycle with transition '{item.get('transition', 'crossfade')}' and effect '{item.get('effect', 'gleam3')}'."
            }

    def _apply_visual(self, resource: dict[str, Any]) -> bool:
        """Apply a visual resource to the canvas and notify listeners."""
        res_type = resource.get("type") or ("image" if resource.get("path") else "unknown")
        url_for_path = resource.get("url_for_path") or self.theater.get_url_for_path
        changed = False

        if res_type == "image":
            raw_path = str(resource.get("path") or "")
            display_path = str(resource.get("display_path") or raw_path)
            display_path = self._ensure_webp_for_display(display_path)
            transition = resource.get("transition") or "crossfade"
            effect = resource.get("effect") or "gleam3"
            prompt = resource.get("prompt") or self._resolve_prompt_for_file(raw_path)
            changed = self.show_image(
                display_path,
                transition=transition,
                effect=effect,
                prompt=prompt,
                clear_animation=True,
                url_for_path=url_for_path,
            )
        elif res_type == "video":
            manifest = resource.get("manifest") or {}
            changed = self.show_video_animation(manifest, url_for_path=url_for_path)
        elif res_type == "layered":
            manifest = resource.get("manifest") or {}
            changed = self.show_layered_animation(manifest, url_for_path=url_for_path)
        elif res_type == "triframe":
            frame_paths = resource.get("frame_paths") or []
            prompt = resource.get("prompt", "")
            changed = self.show_triframe(frame_paths, prompt=prompt, url_for_path=url_for_path)

        self.current_cycle_visual = resource

        if self.on_visual_changed_fn:
            try:
                self.on_visual_changed_fn(changed)
            except Exception as exc:
                logger.error("[VisualState] Exception in on_visual_changed_fn: %s", exc)
        elif self.notify_changed_fn:
            try:
                self.notify_changed_fn("latest")
            except Exception as exc:
                logger.error("[VisualState] Exception in notify_changed_fn: %s", exc)

        if self.on_show_image and res_type == "image":
            try:
                target = resource.get("display_path") or resource.get("path")
                self.on_show_image(
                    self._ensure_webp_for_display(str(target)),
                    transition=resource.get("transition", "crossfade"),
                    effect=resource.get("effect", "gleam3"),
                )
            except Exception as exc:
                logger.error("[VisualState] Exception in on_show_image callback: %s", exc)

        return changed

    def advance_cycle(self) -> Optional[dict]:
        """Advance to the next visual cycle.

        If next_cycle_image is set, promote it to current_cycle_visual,
        display it on the canvas, and clear next_cycle_image.
        If next_cycle_image is None, retain current visual.
        Returns the new current_cycle_visual.
        """
        with self._cycle_lock:
            if self.next_cycle_image is not None:
                staged = self.next_cycle_image
                self.next_cycle_image = None
                logger.info(
                    "[VisualState] Cycle rollover: displaying new visual resource (source=%s)",
                    staged.get("source"),
                )
                self._apply_visual(staged)
                self.current_cycle_visual = staged
                return self.current_cycle_visual
            else:
                logger.debug("[VisualState] Cycle rollover: no next visual staged, retaining current visual.")
                return self.current_cycle_visual

    def _schedule_next_cycle_tick(self) -> None:
        with self._cycle_lock:
            if self._cycle_timer:
                self._cycle_timer.cancel()
                self._cycle_timer = None
            if self.cycle_length > 0:
                self._cycle_timer = threading.Timer(self.cycle_length, self._on_cycle_tick)
                self._cycle_timer.daemon = True
                self._cycle_timer.start()
                self._cycle_active = True

    def stop_cycle(self) -> None:
        """Stop the background visual cycle timer."""
        with self._cycle_lock:
            if self._cycle_timer:
                self._cycle_timer.cancel()
                self._cycle_timer = None
            self._cycle_active = False

    def _on_cycle_tick(self) -> None:
        try:
            self.advance_cycle()
        finally:
            with self._cycle_lock:
                if self._cycle_active and self.cycle_length > 0:
                    self._schedule_next_cycle_tick()

    def __del__(self) -> None:
        try:
            self.stop_cycle()
        except Exception:
            pass

    def initialize_starting_image(self) -> None:
        """Seed an empty canvas from the theater's configured reference image."""
        th = self.theater
        if not th:
            return
        if self.shown_image_path or self._has_generated_image(th):
            return
        cfg = th.config()
        configured = cfg.get("starting_image", "") if isinstance(cfg, dict) else ""
        if not isinstance(configured, str) or not configured.strip():
            return
        image_path = self._find_starting_reference(th, configured.strip())
        if image_path:
            self.show_image(str(image_path), url_for_path=th.get_url_for_path)
            if self.current_cycle_visual is None:
                self.current_cycle_visual = {
                    "type": "image",
                    "path": str(image_path),
                    "transition": "crossfade",
                    "effect": "gleam3",
                    "prompt": self.shown_image_prompt,
                    "source": "starting_image",
                }

    def _resolve_adventure_cover(self, theater: Optional[Theater] = None) -> Optional[Tuple[Path, str]]:
        """Look up adventure cover image from metadata.json if present."""
        th = theater or self.theater
        th_id = self.theater_id or th.theater_id
        if not th_id or not isinstance(th_id, str):
            return None
        theater_dir = th.directory()
        theater_ref_dir = th.references_dir()
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
        meta = None
        td = th.directory()
        if isinstance(td, (str, Path)):
            meta_file = Path(td) / "metadata.json"
            if meta_file.exists():
                try:
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
        image_dir = theater.image_artifacts_dir()
        return image_dir.exists() and any(
            path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            for path in image_dir.rglob("*")
        )

    @staticmethod
    def _find_starting_reference(theater: Theater, image_name: str) -> Path | None:
        reference_dir = theater.references_dir()
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
        if file_path and file_path == self.shown_image_path:
            transition = "none"
        changed = (file_path != self.shown_image_path or effect != self.shown_image_effect)
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
            if (
                not isinstance(last, dict)
                or last.get("path") != file_path
                or last.get("effect") != effect
                or last.get("animation") != animation
            ):
                self.shown_images_history.append(item)
                self.shown_images_history = self.shown_images_history[-100:]
            else:
                self.shown_images_history[-1] = item

        if animation:
            anim_type = str(animation.get("type", "animation"))
            self.current_cycle_visual = {
                "type": anim_type,
                "manifest": animation,
                "id": str(animation.get("id", "")),
                "prompt": prompt,
                "path": file_path,
                "priority": self.PRIORITY_SHOW,
                "source": "show_animation",
            }
        elif file_path:
            self.current_cycle_visual = {
                "type": "image",
                "path": file_path,
                "transition": transition,
                "effect": effect,
                "prompt": prompt,
                "priority": self.PRIORITY_SHOW,
                "source": "show_image",
            }
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
        if self.shown_image_path and self.current_cycle_visual is None:
            self.current_cycle_visual = {
                "type": "image",
                "path": self.shown_image_path,
                "transition": self.shown_image_transition,
                "effect": self.shown_image_effect,
                "prompt": self.shown_image_prompt,
                "source": "loaded_state",
            }

    def serialize(self) -> dict[str, object]:
        return {"current_image_basename": self.current_image_basename, "shown_image_path": self.shown_image_path,
                "shown_image_prompt": self.shown_image_prompt, "shown_images_history": list(self.shown_images_history),
                "shown_image_transition": self.shown_image_transition, "shown_image_effect": self.shown_image_effect,
                "shown_animation_frames": list(self.shown_animation_frames), "shown_layered_animation": self.shown_layered_animation,
                "shown_video_animation": self.shown_video_animation}

    def payload(self) -> dict[str, object]:
        th = self.theater
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
