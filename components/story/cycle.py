"""Canvas-owned, lossless intake with timed batching and one active consumer."""

from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Condition, Timer
import time

from pydantic import JsonValue, TypeAdapter


StoryResult = dict[str, JsonValue]
CycleHandler = Callable[[list[str]], dict[str, StoryResult]]


def story_cycle_length(config: Mapping[str, JsonValue]) -> float:
    """Honor explicit cycle timing and saved theaters' legacy timing options."""
    value = config.get("cycle_length")
    if value is None:
        value = config.get("cooldown_duration", config.get("action_cooldown_base_seconds", 15.0))
    return max(0.0, TypeAdapter(float).validate_python(value))


class StoryCycle:
    """Collect durable input IDs; never call the engine while holding our lock.

    The first pending input opens a collection window. Inputs arriving during
    execution belong to the next window. Completion, including failure, always
    releases the consumer so subsequent input can attempt recovery.
    """

    def __init__(self, cycle_length: float = 15.0) -> None:
        self.cycle_length = max(0.0, cycle_length)
        self._condition = Condition()
        self._handler: CycleHandler | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="story-cycle")
        self._pending: dict[str, Future[StoryResult]] = {}
        self._active: dict[str, Future[StoryResult]] = {}
        self._timer: Timer | None = None
        self._deadline = 0.0
        self._draining = False
        self._closed = False

    def bind(self, handler: CycleHandler) -> None:
        with self._condition:
            if self._closed:
                raise RuntimeError("Story cycle is closed.")
            if self._handler is not None:
                raise RuntimeError("Story cycle already has an engine.")
            self._handler = handler
            self._schedule_locked()

    def submit(self, turn_id: str) -> Future[StoryResult]:
        with self._condition:
            if self._closed:
                raise RuntimeError("Story cycle is closed.")
            existing = self._pending.get(turn_id) or self._active.get(turn_id)
            if existing is not None:
                return existing
            if not self._pending:
                self._deadline = time.monotonic() + self.cycle_length
            future: Future[StoryResult] = Future()
            self._pending[turn_id] = future
            self._schedule_locked()
            return future

    @property
    def is_active(self) -> bool:
        with self._condition:
            return bool(self._active or self._pending)

    @property
    def is_closed(self) -> bool:
        with self._condition:
            return self._closed

    @property
    def is_bound(self) -> bool:
        with self._condition:
            return self._handler is not None

    def payload(self) -> dict[str, JsonValue]:
        with self._condition:
            return {"active": bool(self._active), "pending": len(self._pending), "cycle_length": self.cycle_length}

    def _schedule_locked(self) -> None:
        if self._active or not self._pending or self._handler is None:
            return
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        delay = max(0.0, self._deadline - time.monotonic())
        if self._draining or self._closed or delay == 0:
            self._active, self._pending = self._pending, {}
            self._executor.submit(self._run, self._handler, self._active)
        else:
            self._timer = Timer(delay, self._tick)
            self._timer.daemon = True
            self._timer.start()

    def _tick(self) -> None:
        with self._condition:
            self._schedule_locked()

    def _run(self, handler: CycleHandler, batch: dict[str, Future[StoryResult]]) -> None:
        try:
            results = handler(list(batch))
            for turn_id, future in batch.items():
                if not future.cancelled():
                    future.set_result(results[turn_id])
        except BaseException as exc:
            for future in batch.values():
                if not future.done():
                    future.set_exception(exc)
            raise
        finally:
            with self._condition:
                self._active = {}
                self._schedule_locked()
                self._condition.notify_all()

    def flush(self) -> None:
        """Release the current collection window without waiting for execution."""
        with self._condition:
            self._deadline = 0.0
            self._schedule_locked()

    def wait(self, timeout: float = 5.0) -> bool:
        """Drain collection windows for deterministic harnesses and shutdown."""
        with self._condition:
            self._draining = True
            try:
                self._schedule_locked()
                return self._condition.wait_for(lambda: not self._active and not self._pending, timeout)
            finally:
                self._draining = False

    def close(self) -> None:
        with self._condition:
            self._closed = True
            abandoned = list(self._pending.values()) if self._handler is None else []
            if abandoned:
                self._pending = {}
            self._schedule_locked()
            self._condition.wait_for(lambda: not self._active and not self._pending)
        for future in abandoned:
            future.cancel()
        self._executor.shutdown(wait=True)
