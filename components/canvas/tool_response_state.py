"""Transient agent-tool and response indicator state."""

import time
from collections.abc import Callable
from typing import TypedDict


class ToolActivityPayload(TypedDict):
    image_generating: bool
    animation_generating: bool
    user_action_processing: bool
    live_ready: bool
    dice_rolling: bool
    dice_result: dict[str, object] | None


class ToolResponseState:
    def __init__(self, notify_changed: Callable[..., None]) -> None:
        self._notify_changed = notify_changed
        self.image_generation_active = False; self.animation_generation_active = False
        self.user_action_processing_active = False; self.live_connection_active = False
        self.live_connection_until = 0.0; self.dice_roll_until = 0.0
        self.dice_roll_result: dict[str, object] | None = None
        self.current_agent_thought = ""; self.current_agent_thought_time = 0.0

    def set_activity(self, tool: str, active: bool = True, recent_seconds: float = 5.0,
                     result: dict[str, object] | None = None) -> None:
        if tool == "image": self.image_generation_active = bool(active)
        elif tool == "animation": self.animation_generation_active = bool(active)
        elif tool == "user_action": self.user_action_processing_active = bool(active)
        elif tool == "live":
            self.live_connection_active = bool(active)
            self.live_connection_until = time.time() + max(0.0, recent_seconds) if active and recent_seconds > 0 else 0.0
        elif tool == "dice":
            self.dice_roll_until = time.time() + max(0.0, recent_seconds) if active and recent_seconds > 0 else 0.0
            self.dice_roll_result = dict(result) if active and result else None
        self._notify_changed("latest")

    def activity_payload(self) -> ToolActivityPayload:
        dice_rolling = time.time() < self.dice_roll_until
        return {"image_generating": self.image_generation_active, "animation_generating": self.animation_generation_active,
                "user_action_processing": self.user_action_processing_active,
                "live_ready": self.live_connection_active or time.time() < self.live_connection_until,
                "dice_rolling": dice_rolling, "dice_result": self.dice_roll_result if dice_rolling else None}

    def set_agent_thought(self, text: str, maximum_length: int = 360) -> None:
        normalized = text.strip()
        self.current_agent_thought = normalized[:maximum_length - 1].rstrip() + "…" if len(normalized) > maximum_length else normalized
        self.current_agent_thought_time = time.time() if normalized else 0.0
        self._notify_changed("latest")

    def agent_thought_payload(self) -> dict[str, object]:
        return {"text": self.current_agent_thought, "time": self.current_agent_thought_time}
