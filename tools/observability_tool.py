"""Optional agent tool for requesting an immediate canvas observation."""

from __future__ import annotations

from typing import Callable, Optional

from tools.base_tool import BaseTools, with_cooldown


class ObservabilityTools(BaseTools):
    """Expose an agent-controlled, cooldown-protected observation request."""

    def __init__(self, config: Optional[dict] = None, theater_id: str = "") -> None:
        super().__init__(
            config=config,
            theater_id=theater_id,
            default_cooldown=30.0,
        )
        self.on_observability_requested: Optional[Callable[[], bool]] = None

    @with_cooldown("requesting another canvas observability update")
    def request_canvas_observability(self) -> str:
        """Request the current canvas state when it would help continue the story.

        Use sparingly: this interrupts the normal observability cadence and is
        subject to a cooldown.
        """
        callback = self.on_observability_requested
        if not callable(callback):
            return "Error: Canvas observability is not available for this session."
        if not callback():
            return "Error: Canvas observability could not be sent because no live session is connected."
        return "Current canvas state sent. The next regular update has been postponed."
