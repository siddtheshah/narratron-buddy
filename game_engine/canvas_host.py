"""Host boundary for committing panel actions and canvas output together."""
from __future__ import annotations

from _thread import RLock
from contextlib import AbstractContextManager
from typing import Protocol

from pydantic import JsonValue

from game_engine.canvas_contract import CanvasSnapshot


class CanvasHost(Protocol):
    canvas_lock: RLock

    def canvas_observation(self) -> CanvasSnapshot: ...

    def stage_canvas_effects(
        self, effects: list[JsonValue], snapshot: CanvasSnapshot,
    ) -> AbstractContextManager[CanvasSnapshot]: ...
