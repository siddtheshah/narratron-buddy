"""Visual presentation state for a canvas theater."""

import time
from collections.abc import Callable
from pathlib import Path
import re
from typing import Any


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
