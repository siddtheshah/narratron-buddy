"""Tools for generating small, loop-ready image animation sequences."""

from __future__ import annotations

import logging
import json
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Union

from pydantic import TypeAdapter
from providers.animation_provider import AnimationProvider, AnimationTechnique, AnimationLayer as AnimationLayer

from providers import (
    ImageProvider,
    ImageReference,
    TextResponseProvider,
    VideoProvider,
)
from providers.registry import get_animation_provider
from providers.fal_qwen_layered_provider import FalQwenLayeredProvider
from tools.base_tool import BaseTools, blocked_when_canvas_pinned, logged_tool_call, with_cycle_cooldown
from tools.tool_metadata import terminal
from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from components.canvas.visual_state import VisualState
from components.reference_manager import ReferenceManager


logger = logging.getLogger(__name__)


class AnimationTools(BaseTools):
    """Create animated visual assets using the theater's shared visuals configuration."""

    def __init__(
        self,
        theater: Theater,
        canvas_manager: CanvasStateManager,
        text_response_provider: TextResponseProvider,
        layered_provider: FalQwenLayeredProvider,
        image_provider: Optional[ImageProvider] = None,
        video_provider: Optional[VideoProvider] = None,
        reference_manager: Optional[ReferenceManager] = None,
    ) -> None:
        super().__init__(
            theater=theater,
            canvas_manager=canvas_manager,
        )
        self.reference_manager: Optional[ReferenceManager] = (
            reference_manager if reference_manager is not None else (ReferenceManager(theater=theater) if theater is not None else None)
        )
        self.animation_provider = get_animation_provider(
            options=self.config,
            image_provider=image_provider, text_response_provider=text_response_provider,
            layered_provider=layered_provider, video_provider=video_provider,
        )
        self.animation_config = self.animation_provider.animation_config
        self.cooldown_duration = float(self.animation_config.get("cooldown_duration") or 0)
        self.output_dir = str(theater.directory())
        self.animations_dir = str(theater.animations_dir())
        Path(self.animations_dir).mkdir(parents=True, exist_ok=True)
        self.default_style = self.animation_provider.default_style
        self._animations: dict[str, list[str]] = {}
        self._layered_animations: dict[str, dict] = {}
        self._video_animations: dict[str, dict] = {}
        self.on_animation_ready: Callable[..., None] | None = None
        self.on_animation_created: Callable[..., None] | None = None
        self.on_layered_animation_created: Callable[..., None] | None = None
        self.on_image_created: Callable[..., None] | None = None
        self.is_generating: bool = False
        self._last_generation_thread: threading.Thread | None = None

    @property
    def visual(self) -> Optional[VisualState]:
        return self.canvas_manager.visual if self.canvas_manager is not None else None

    def _trigger_after_tool_call(self, tool_name: str) -> None:
        callback = self.on_after_tool_call
        if callback:
            try:
                callback(tool_name, {})
            except Exception:
                logger.exception("[AnimationTools] After-tool callback failed for %s", tool_name)

    def _set_canvas_activity(self, active: bool) -> None:
        """Notify connected canvases that animation generation has started or finished."""
        self.is_generating = bool(active)
        if not active:
            self.theater.storage_changed()
        self.canvas_manager.tool_response.set_activity("animation", active=active)

    def join_generation(self, timeout: float = 30.0) -> None:
        """Wait for the latest animation generation; useful in tests and teardown."""
        thread = self._last_generation_thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    @terminal
    @blocked_when_canvas_pinned
    @with_cycle_cooldown(action_desc="generating another animation")
    def create_animation(
        self,
        scene_prompt: str,
        animation_name: str,
        reference_images: Union[list[str], str, None] = None,
        technique: Optional[str] = None,
    ) -> str:
        """Generate an animation sequence by automatically deciding between triframe, layered, and video techniques.

        The caller supplies a scene prompt and animation name. If forced_technique is set in config,
        it bypasses the classification decision and uses that technique directly. Otherwise, an internal LLM decision prompt determines whether to use:
        - 'triframe' for complex motions and transitions
        - 'layered' for scenic backdrops or high-energy single-moment climaxes
        - 'video' for cinematic, continuous motion or fluid natural action scenes

        Args:
            scene_prompt: Detailed scene prompt. Use <Character Name> tags from Available
                Character Visuals to attach their latest portraits automatically.
            animation_name: Friendly name used for saved animation files.
            reference_images: Optional scene names, image aliases, or canvas capture handles to use.
            technique: Optional manual override ('triframe', 'layered', or 'video').

        Returns:
            A status message immediately; animation generation continues in background.
        """
        if not scene_prompt.strip():
            return "Error: scene_prompt is required."
        if not animation_name.strip():
            return "Error: animation_name is required."

        provider_references, reference_error = self._resolve_provider_references(
            reference_images, scene_prompt=scene_prompt.strip()
        )
        if reference_error:
            return reference_error

        self.record_tool_call("create_animation")
        timestamp = int(time.time())
        clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", animation_name.strip())
        animation_id = f"{clean_name}_{timestamp}"

        def _worker() -> None:
            self._set_canvas_activity(True)
            try:
                self._generate_animation(scene_prompt.strip(), animation_id, provider_references, technique)
            except Exception:
                logger.exception("[AnimationTools] Failed to create animation for %s", animation_id)
            finally:
                self._set_canvas_activity(False)
                self._trigger_after_tool_call("create_animation")

        thread = threading.Thread(target=_worker, daemon=True)
        self._last_generation_thread = thread
        thread.start()
        return (
            f"Animation generation started in background with name '{animation_name.strip()}'. "
            f"Animation ID: '{animation_id}'. Call play_animation with that ID once it is ready."
        )

    # Compatibility aliases for existing planner consumers.
    plan_animation_technique_with_provider = staticmethod(AnimationProvider.plan_animation_technique_with_provider)
    plan_triframe_with_provider = staticmethod(AnimationProvider.plan_triframe_with_provider)
    plan_layers_with_provider = staticmethod(AnimationProvider.plan_layers_with_provider)
    _flatten_layer_plan = staticmethod(AnimationProvider._flatten_layer_plan)
    _decomposition_prompt = staticmethod(AnimationProvider._decomposition_prompt)
    _apply_video_style = AnimationProvider._apply_video_style

    def _generate_animation(
        self, scene_prompt: str, animation_id: str,
        references: list[ImageReference], technique: str | None,
    ) -> None:
        selected = self.animation_provider.create_animation(
            scene_prompt, animation_id, output_dir=self.theater.directory(),
            technique=TypeAdapter(AnimationTechnique | None).validate_python(technique),
            references=references,
        )
        if selected == "triframe":
            for index, path in enumerate(self._find_triframe_animation(animation_id), start=1):
                self._register_frame_aliases(animation_id, index, path)
        elif selected == "layered":
            manifest = self._find_layered_animation(animation_id)
            if manifest:
                self._register_layered_aliases(animation_id, manifest["base_image"], [layer["path"] for layer in manifest["layers"]])
        else:
            start_frame_path = Path(self.animations_dir) / animation_id / "start_frame.webp"
            if self.visual:
                self.visual.register_image(str(Path(self.animations_dir) / animation_id / "video.mp4"), f"{animation_id}_video", animation_id)
                if start_frame_path.is_file():
                    self.visual.register_image(str(start_frame_path), f"{animation_id}_start_frame")
            if start_frame_path.is_file():
                self._notify_image_created(str(start_frame_path))
        self._notify_animation_created(animation_id)
        if selected == "layered":
            self._notify_layered_animation_created(animation_id)
        self._notify_animation_ready(animation_id, selected)

    def _register_layered_aliases(self, animation_id: str, base_path: str, layer_paths: list[str]) -> None:
        if not self.visual:
            return
        self.visual.register_image(base_path, f"{animation_id}_base")
        for index, path in enumerate(layer_paths, start=1):
            self.visual.register_image(path, f"{animation_id}_layer_{index}")

    def _to_relative_path(self, path: str | Path) -> str:
        output_dir_path = Path(self.output_dir)
        try:
            return Path(path).relative_to(output_dir_path).as_posix()
        except ValueError:
            parts = Path(path).parts
            if "animations" in parts:
                idx = parts.index("animations")
                return Path(*parts[idx:]).as_posix()
            return Path(path).name

    def _find_layered_animation(self, animation_id: str) -> Optional[dict]:
        if animation_id in self._layered_animations:
            return self._layered_animations[animation_id]
        clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", animation_id)
        animation_dir = Path(self.animations_dir) / clean_id
        manifest_path = animation_dir / "layered.json"
        if not manifest_path.is_file():
            manifest_path = animation_dir / "animation.json"
        if not manifest_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest.get("layers"), list) or len(manifest["layers"]) < 2:
                return None
            output_dir_path = Path(self.output_dir)
            for layer in manifest.get("layers", []):
                if isinstance(layer, dict) and "path" in layer:
                    layer_path = Path(layer["path"])
                    abs_path = (output_dir_path / layer_path) if not layer_path.is_absolute() else layer_path
                    if not abs_path.is_file():
                        local_path = animation_dir / layer_path.name
                        if local_path.is_file():
                            abs_path = local_path
                    layer["path"] = str(abs_path)
            if "base_image" in manifest:
                base_path = Path(manifest["base_image"])
                abs_base = (output_dir_path / base_path) if not base_path.is_absolute() else base_path
                if not abs_base.is_file():
                    local_base = animation_dir / base_path.name
                    if local_base.is_file():
                        abs_base = local_base
                manifest["base_image"] = str(abs_base)
            self._layered_animations[clean_id] = manifest
            return manifest
        except (OSError, json.JSONDecodeError):
            logger.warning("[AnimationTools] Invalid layered animation manifest: %s", manifest_path)
            return None

    def _find_video_animation(self, animation_id: str) -> Optional[dict]:
        clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", animation_id)
        if clean_id in self._video_animations:
            return self._video_animations[clean_id]
        if animation_id in self._video_animations:
            return self._video_animations[animation_id]

        anim_dir = Path(self.animations_dir) / clean_id
        manifest_path = anim_dir / "video.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                video_path = anim_dir / "video.mp4"
                output_dir_path = Path(self.output_dir)
                if "video_path" in manifest:
                    vp = Path(manifest["video_path"])
                    abs_vp = (output_dir_path / vp) if not vp.is_absolute() else vp
                    if abs_vp.is_file():
                        manifest["video_path"] = str(abs_vp)
                    elif video_path.is_file():
                        manifest["video_path"] = str(video_path)
                elif video_path.is_file():
                    manifest["video_path"] = str(video_path)
                if "start_image" in manifest:
                    sip = Path(manifest["start_image"])
                    abs_sip = (output_dir_path / sip) if not sip.is_absolute() else sip
                    if abs_sip.is_file():
                        manifest["start_image"] = str(abs_sip)
                if "start_frame" in manifest:
                    sfp = Path(manifest["start_frame"])
                    abs_sfp = (output_dir_path / sfp) if not sfp.is_absolute() else sfp
                    if abs_sfp.is_file():
                        manifest["start_frame"] = str(abs_sfp)
                manifest.setdefault(
                    "video_duration_seconds",  5
                )
                manifest.pop("duration", None)
                manifest.pop("duration_seconds", None)
                self._video_animations[clean_id] = manifest
                return manifest
            except (OSError, json.JSONDecodeError):
                logger.warning("[AnimationTools] Invalid video animation manifest: %s", manifest_path)
                return None

        video_path = anim_dir / "video.mp4"
        if video_path.is_file():
            manifest = {
                "id": clean_id,
                "type": "video",
                "scene_prompt": "",
                "video_path": str(video_path),
                "video_duration_seconds": 5,
                "loop": True,
                "muted": True,
            }
            self._video_animations[clean_id] = manifest
            return manifest

        return None

    def _resolve_provider_references(
        self,
        reference_images: Union[list[str], str, None],
        scene_prompt: str = "",
    ) -> tuple[list[ImageReference], Optional[str]]:
        if self.reference_manager is not None:
            return self.reference_manager.resolve_provider_references(
                reference_images=reference_images,
                prompt=scene_prompt,
                visual=self.visual,
                caller_label="AnimationTools",
            )
        return [], None

    @terminal
    @blocked_when_canvas_pinned
    @with_cycle_cooldown(action_desc="playing another animation")
    def play_animation(self, animation_id: str) -> str:
        """Display a saved animation (triframe or layered) on the canvas.

        Args:
            animation_id: The ID returned by create_animation.
        """
        url_resolver = self.theater.get_url_for_path
        manifest = self._find_video_animation(animation_id)
        if manifest:
            res = self.canvas_manager.visual.update_animation(
                "video",
                manifest,
                id=animation_id,
                priority=self.canvas_manager.visual.PRIORITY_SHOW,
                source="play_animation",
                url_for_path=url_resolver,
            )
            self._trigger_after_tool_call("play_animation")
            status = res.get("status")
            if status == "queued":
                return f"Video animation '{animation_id}' queued for its turn on the canvas."
            elif status == "blocked":
                return f"Error: {res['message']}"
            return f"Playing video animation '{animation_id}'."

        manifest = self._find_layered_animation(animation_id)
        if manifest:
            res = self.canvas_manager.visual.update_animation(
                "layered",
                manifest,
                id=animation_id,
                priority=self.canvas_manager.visual.PRIORITY_SHOW,
                source="play_animation",
                url_for_path=url_resolver,
            )
            self._trigger_after_tool_call("play_animation")
            status = res.get("status")
            if status == "queued":
                return f"Layered animation '{animation_id}' queued for its turn on the canvas."
            elif status == "blocked":
                return f"Error: {res['message']}"
            return f"Playing layered animation '{animation_id}'."

        frame_paths = self._find_triframe_animation(animation_id)
        if frame_paths:
            res = self.canvas_manager.visual.update_animation(
                "triframe",
                frame_paths,
                id=animation_id,
                priority=self.canvas_manager.visual.PRIORITY_SHOW,
                source="play_animation",
                url_for_path=url_resolver,
            )
            self._trigger_after_tool_call("play_animation")
            status = res.get("status")
            if status == "queued":
                return f"Animation '{animation_id}' queued for its turn on the canvas."
            elif status == "blocked":
                return f"Error: {res['message']}"
            return f"Playing animation '{animation_id}'."

        return f"Error: Animation '{animation_id}' was not found."


    def _register_frame_aliases(
        self,
        animation_id: str,
        frame_number: int,
        filepath: str,
    ) -> None:
        if self.visual:
            self.visual.register_image(filepath, f"{animation_id}_frame_{frame_number}")

    def _find_triframe_animation(self, animation_id: str) -> list[str]:
        if animation_id in self._animations:
            return self._animations[animation_id]
        clean_id = re.sub(r"[^a-zA-Z0-9_-]", "_", animation_id)
        animation_dir = Path(self.animations_dir) / clean_id
        manifest_path = animation_dir / "triframe.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(manifest.get("frames"), list) and manifest["frames"]:
                    output_dir_path = Path(self.output_dir)
                    abs_frames = []
                    for f in manifest["frames"]:
                        fp = Path(f)
                        abs_fp = (output_dir_path / fp) if not fp.is_absolute() else fp
                        if not abs_fp.is_file():
                            local_fp = animation_dir / fp.name
                            if local_fp.is_file():
                                abs_fp = local_fp
                        abs_frames.append(str(abs_fp))
                    if all(Path(p).is_file() for p in abs_frames):
                        self._animations[clean_id] = abs_frames
                        return abs_frames
            except (OSError, json.JSONDecodeError):
                pass
        for extension in ("webp", "jpg"):
            frame_paths = [str(animation_dir / f"frame_{number}.{extension}") for number in range(1, 4)]
            if all(Path(path).is_file() for path in frame_paths):
                self._animations[clean_id] = frame_paths
                return frame_paths
        return []

    @logged_tool_call
    def browse_animations(self) -> list[dict[str, str]]:
        """Browse all available saved animations (both triframe and layered).

        Returns:
            A list of animation IDs, types, and scene prompts for play_animation.
        """
        try:
            results = []
            seen_ids = set()
            anim_base_dir = Path(self.animations_dir)
            if anim_base_dir.exists():
                for subfolder in sorted(anim_base_dir.iterdir()):
                    if subfolder.is_dir():
                        info = self._load_animation_info(subfolder)
                        if info:
                            seen_ids.add(info["id"])
                            results.append(info)

            for anim_id, manifest in self._layered_animations.items():
                if anim_id not in seen_ids:
                    seen_ids.add(anim_id)
                    results.append({
                        "id": anim_id,
                        "type": "layered",
                        "scene_prompt": manifest.get("scene_prompt", ""),
                        "base_image": manifest.get("base_image", ""),
                        "layers": manifest.get("layers", []),
                    })
            for anim_id, manifest in self._video_animations.items():
                if anim_id not in seen_ids:
                    seen_ids.add(anim_id)
                    results.append({
                        "id": anim_id,
                        "type": "video",
                        "scene_prompt": manifest.get("scene_prompt", ""),
                        "video_path": self._to_relative_path(manifest.get("video_path", "")),
                        "video_url": manifest.get("video_url"),
                    })
            for anim_id, frames in self._animations.items():
                if anim_id not in seen_ids:
                    seen_ids.add(anim_id)
                    results.append({
                        "id": anim_id,
                        "type": "triframe",
                        "scene_prompt": "",
                        "frames": [self._to_relative_path(p) for p in frames],
                    })

            self._trigger_after_tool_call("browse_animations")
            return [
                {key: info[key] for key in ("id", "type", "scene_prompt")}
                for info in results
            ]
        except Exception as exc:
            logger.exception("[AnimationTools] Failed to browse animations: %s", exc)
            self._trigger_after_tool_call("browse_animations")
            return []

    def _load_animation_info(self, subfolder: Path) -> Optional[dict]:
        anim_id = subfolder.name
        layered_manifest_path = subfolder / "layered.json"
        if not layered_manifest_path.is_file():
            layered_manifest_path = subfolder / "animation.json"
        if layered_manifest_path.is_file():
            try:
                manifest = json.loads(layered_manifest_path.read_text(encoding="utf-8"))
                if isinstance(manifest, dict) and "layers" in manifest:
                    return {
                        "id": manifest.get("id", anim_id),
                        "type": "layered",
                        "scene_prompt": manifest.get("scene_prompt", ""),
                        "base_image": manifest.get("base_image", ""),
                        "layers": manifest.get("layers", []),
                    }
            except (OSError, json.JSONDecodeError):
                pass

        triframe_manifest_path = subfolder / "triframe.json"
        if triframe_manifest_path.is_file():
            try:
                manifest = json.loads(triframe_manifest_path.read_text(encoding="utf-8"))
                if isinstance(manifest, dict):
                    return {
                        "id": manifest.get("id", anim_id),
                        "type": "triframe",
                        "scene_prompt": manifest.get("scene_prompt", ""),
                        "frames": manifest.get("frames", []),
                    }
            except (OSError, json.JSONDecodeError):
                pass

        video_manifest_path = subfolder / "video.json"
        if video_manifest_path.is_file():
            try:
                manifest = json.loads(video_manifest_path.read_text(encoding="utf-8"))
                if isinstance(manifest, dict):
                    return {
                        "id": manifest.get("id", anim_id),
                        "type": "video",
                        "scene_prompt": manifest.get("scene_prompt", ""),
                        "video_path": manifest.get("video_path", self._to_relative_path(subfolder / "video.mp4")),
                        "video_url": manifest.get("video_url"),
                        "video_duration_seconds": manifest.get("video_duration_seconds", manifest.get("duration_seconds", manifest.get("duration", 5))),
                        "start_image": manifest.get("start_image", manifest.get("start_frame")),
                    }
            except (OSError, json.JSONDecodeError):
                pass

        if (subfolder / "video.mp4").is_file():
            return {
                "id": anim_id,
                "type": "video",
                "scene_prompt": "",
                "video_path": self._to_relative_path(subfolder / "video.mp4"),
                "video_duration_seconds": 5,
            }

        for extension in ("webp", "jpg"):
            frame_paths = [str(subfolder / f"frame_{number}.{extension}") for number in range(1, 4)]
            if all(Path(p).is_file() for p in frame_paths):
                return {
                    "id": anim_id,
                    "type": "triframe",
                    "scene_prompt": "",
                    "frames": [self._to_relative_path(p) for p in frame_paths],
                }
        return None

    def _notify_image_created(self, filepath: str) -> None:
        callback = self.on_image_created
        if callback:
            try:
                callback(filepath)
            except TypeError:
                try:
                    callback()
                except Exception:
                    logger.exception("[AnimationTools] Image-created callback failed")
            except Exception:
                logger.exception("[AnimationTools] Image-created callback failed")

    def _notify_layered_animation_created(self, animation_id: str) -> None:
        callback = self.on_layered_animation_created
        if callback:
            try:
                callback(animation_id)
            except TypeError:
                try:
                    callback()
                except Exception:
                    logger.exception("[AnimationTools] Layered-animation-created callback failed")
            except Exception:
                logger.exception("[AnimationTools] Layered-animation-created callback failed")

    def _notify_animation_created(self, animation_id: str) -> None:
        """Notify billing only after a complete, playable animation exists."""
        callback = self.on_animation_created
        if callback:
            try:
                callback(animation_id)
            except TypeError:
                try:
                    callback()
                except Exception:
                    logger.exception("[AnimationTools] Animation-created callback failed")
            except Exception:
                logger.exception("[AnimationTools] Animation-created callback failed")

    def _notify_animation_ready(self, animation_id: str, technique: str) -> None:
        callback = self.on_animation_ready
        if callback:
            try:
                callback(animation_id, technique)
            except TypeError:
                try:
                    callback(animation_id)
                except Exception:
                    logger.exception("[AnimationTools] Animation-ready callback failed")
            except Exception:
                logger.exception("[AnimationTools] Animation-ready callback failed")
