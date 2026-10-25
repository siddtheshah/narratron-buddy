"""Display story output with one current scene and one replaceable waiting scene."""

from collections.abc import Callable, Mapping
import logging
from threading import Condition, Timer
import time

from pydantic import BaseModel, Field, JsonValue, TypeAdapter


logger = logging.getLogger(__name__)


def story_output_cycle_length(config: Mapping[str, JsonValue]) -> float:
    """Read output display time independently of input gathering."""
    value = config.get("output_cycle_length")
    return max(0.0, TypeAdapter(float).validate_python(15.0 if value is None else value))


class StoryOutput(BaseModel):
    sequence: int
    narration: str
    dialogue: list[dict[str, JsonValue]] = Field(default_factory=list)


class OutputCheckpoint(BaseModel):
    accepted_sequence: int = 0
    displayed_sequence: int = 0
    pending: StoryOutput | None = None


class StoryOutputDisplayCycle:
    """Display the first scene immediately, then keep only the newest waiting one.

    Sequence numbers come from input journal positions, not completion times.
    Presentation callbacks run outside the bookkeeping lock, one at a time.
    The display interval starts after presentation commits. Its completion
    promotes the newest waiting scene; later arrivals never extend the interval.
    """

    def __init__(
        self,
        present: Callable[[StoryOutput], None],
        changed: Callable[[], None],
        cycle_length: float = 15.0,
    ) -> None:
        self.cycle_length = max(0.0, cycle_length)
        self._present = present
        self._changed = changed
        self._condition = Condition()
        self._checkpoint = OutputCheckpoint()
        self._active: StoryOutput | None = None
        self._deadline = 0.0
        self._timer: Timer | None = None
        self._timer_revision = 0
        self._closed = False

    def submit(self, narration: str, dialogue: list[dict[str, JsonValue]], sequence: int | None = None) -> None:
        with self._condition:
            if self._closed:
                raise RuntimeError("Story output cycle is closed.")
            position = self._checkpoint.accepted_sequence + 1 if sequence is None else sequence
            if position < self._checkpoint.accepted_sequence:
                return
            if position > self._checkpoint.accepted_sequence:
                pending = StoryOutput(sequence=position, narration=narration, dialogue=dialogue)
                self._checkpoint.accepted_sequence = position
                self._checkpoint.pending = pending
            # An equal sequence may retry a failed presentation or checkpoint
            # write, but can never replay a scene that was already displayed.
            output = self._take_locked()
        if output is not None:
            self._show(output)
        else:
            # Persist the waiting slot before the engine acknowledges delivery.
            self._changed()

    def _cancel_timer_locked(self) -> None:
        self._timer_revision += 1
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _take_locked(self, *, force: bool = False) -> StoryOutput | None:
        if self._active is not None or self._checkpoint.pending is None or self._closed:
            return None
        self._cancel_timer_locked()
        delay = max(0.0, self._deadline - time.monotonic())
        if not force and delay > 0:
            self._timer = Timer(delay, self._tick, args=(self._timer_revision,))
            self._timer.daemon = True
            self._timer.start()
            return None
        self._active = self._checkpoint.pending
        self._checkpoint.pending = None
        return self._active

    def _show(self, output: StoryOutput) -> None:
        current: StoryOutput | None = output
        while current is not None:
            try:
                self._present(current)
                with self._condition:
                    self._checkpoint.displayed_sequence = current.sequence
                    self._deadline = time.monotonic() + self.cycle_length
                self._changed()
            except BaseException:
                with self._condition:
                    if self._checkpoint.pending is None and current.sequence > self._checkpoint.displayed_sequence:
                        self._checkpoint.pending = current
                    self._active = None
                    self._condition.notify_all()
                raise
            with self._condition:
                self._active = None
                current = self._take_locked()
                self._condition.notify_all()

    def _tick(self, revision: int) -> None:
        with self._condition:
            if revision != self._timer_revision:
                return
            output = self._take_locked()
        if output is not None:
            try:
                self._show(output)
            except Exception:
                logger.exception("Story output remains pending after presentation failure")

    def flush(self, timeout: float = 5.0) -> bool:
        """Drain the latest waiting scene for harnesses and shutdown."""
        deadline = time.monotonic() + timeout
        while True:
            with self._condition:
                if not self._condition.wait_for(lambda: self._active is None, max(0.0, deadline - time.monotonic())):
                    return False
                output = self._take_locked(force=True)
                if output is None:
                    return self._checkpoint.pending is None
            self._show(output)

    def snapshot(self) -> dict[str, JsonValue]:
        with self._condition:
            checkpoint = self._checkpoint.model_copy(deep=True)
            if checkpoint.pending is None and self._active is not None and self._active.sequence > checkpoint.displayed_sequence:
                checkpoint.pending = self._active
            return checkpoint.model_dump(mode="json")

    def restore(self, checkpoint: OutputCheckpoint) -> None:
        """Restore without starting callbacks while canvas hydration is incomplete."""
        with self._condition:
            self._cancel_timer_locked()
            self._checkpoint = checkpoint.model_copy(deep=True)
            self._deadline = time.monotonic() + self.cycle_length if checkpoint.displayed_sequence else 0.0

    def start(self) -> None:
        with self._condition:
            self._closed = False
            output = self._take_locked()
        if output is not None:
            self._show(output)

    def payload(self) -> dict[str, JsonValue]:
        with self._condition:
            return {
                "cycle_length": self.cycle_length,
                "active": self._active is not None,
                "pending": self._checkpoint.pending is not None,
                "accepted_sequence": self._checkpoint.accepted_sequence,
                "displayed_sequence": self._checkpoint.displayed_sequence,
            }

    def close(self) -> None:
        """Cancel scheduled presentation; retain the persisted waiting slot."""
        with self._condition:
            self._closed = True
            self._cancel_timer_locked()
            self._condition.wait_for(lambda: self._active is None)
