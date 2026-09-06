"""Visual presentation state for a canvas theater."""

from typing import Protocol


class VisualManager(Protocol):
    def update_shown_image(self, file_path: str, **kwargs: object) -> None: ...
    def show_triframe(self, frame_paths: list[str], **kwargs: object) -> None: ...
    def show_layered_animation(self, manifest: dict[str, object], **kwargs: object) -> None: ...
    def show_video_animation(self, manifest: dict[str, object], **kwargs: object) -> None: ...


class VisualState:
    def __init__(self) -> None:
        self.current_image_basename: str | None = None; self.shown_image_path: str | None = None
        self.shown_image_time = 0.0; self.shown_image_prompt = ""; self.shown_images_history: list[object] = []
        self.shown_image_transition = "crossfade"; self.shown_image_effect = "gleam3"
        self.shown_animation_frames: list[str] = []; self.shown_layered_animation: dict[str, object] | None = None
        self.shown_video_animation: dict[str, object] | None = None; self.image_revision = 0

    def update_image(self, manager: VisualManager, file_path: str, **kwargs: object) -> None:
        """Present an image through the manager's compatibility pipeline."""
        manager.update_shown_image(file_path, **kwargs)

    def show_triframe(self, manager: VisualManager, frame_paths: list[str], **kwargs: object) -> None:
        manager.show_triframe(frame_paths, **kwargs)

    def show_layered_animation(self, manager: VisualManager, manifest: dict[str, object], **kwargs: object) -> None:
        manager.show_layered_animation(manifest, **kwargs)

    def show_video_animation(self, manager: VisualManager, manifest: dict[str, object], **kwargs: object) -> None:
        manager.show_video_animation(manifest, **kwargs)

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
            result["animation"] = {"type": "triframe", "frame_paths": self.shown_animation_frames}
        elif self.shown_layered_animation: result["animation"] = {"type": "layered", **self.shown_layered_animation}
        elif self.shown_video_animation: result["animation"] = {"type": "video", **self.shown_video_animation}
        return result
