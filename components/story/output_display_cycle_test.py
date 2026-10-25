"""Story display pacing preserves input order and replaces waiting scenes."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock, patch

import pytest
from pydantic import JsonValue

from components.story.output_display_cycle import (
    OutputCheckpoint,
    StoryOutput,
    StoryOutputDisplayCycle,
    story_output_cycle_length,
)


@pytest.mark.parametrize(("config", "expected"), [
    ({}, 15.0),
    ({"output_cycle_length": None}, 15.0),
    ({"input_cycle_length": 2, "cycle_length": 8, "cooldown_duration": 4}, 15.0),
    ({"output_cycle_length": 0}, 0.0),
    ({"output_cycle_length": 30, "input_cycle_length": 1}, 30.0),
    ({"output_cycle_length": -1}, 0.0),
])
def test_output_timing_is_independent(config: dict[str, JsonValue], expected: float) -> None:
    assert story_output_cycle_length(config) == expected


@pytest.fixture
def clock() -> Iterator[Mock]:
    with patch("components.story.output_display_cycle.time.monotonic", return_value=100.0) as now, patch(
        "components.story.output_display_cycle.Timer",
    ):
        yield now


def test_output_display_replaces_waiting_content_without_restarting_current_dwell(clock: Mock) -> None:
    present = Mock()
    cycle = StoryOutputDisplayCycle(present, Mock(), 10)
    cycle.submit("one", [], 1)
    present.assert_called_once_with(StoryOutput(sequence=1, narration="one"))
    assert not cycle.payload()["pending"]
    clock.return_value = 102.0
    cycle.submit("two", [], 2)
    old_tick = cycle._timer_revision
    clock.return_value = 104.0
    cycle.submit("three", [{"speaker": "Mara", "text": "Third"}], 3)
    assert [call.args[0].sequence for call in present.call_args_list] == [1]
    clock.return_value = 110.0
    cycle._tick(old_tick)
    assert present.call_count == 1
    cycle._tick(cycle._timer_revision)
    assert [call.args[0].sequence for call in present.call_args_list] == [1, 3]
    assert present.call_args.args[0].dialogue == [{"speaker": "Mara", "text": "Third"}]
    cycle.submit("late two", [], 2)
    cycle.submit("duplicate three", [], 3)
    assert cycle.flush()
    assert present.call_count == 2
    cycle.close()


def test_preparing_content_does_not_consume_its_display_interval(clock: Mock) -> None:
    shown: list[int] = []

    def present(output: StoryOutput) -> None:
        if output.sequence == 1:
            clock.return_value = 120.0
        shown.append(output.sequence)

    cycle = StoryOutputDisplayCycle(present, Mock(), 10)
    try:
        cycle.submit("first", [], 1)
        assert shown == [1]
        clock.return_value = 121.0
        cycle.submit("second", [], 2)
        clock.return_value = 125.0
        cycle.submit("third", [], 3)
        clock.return_value = 129.0
        cycle._tick(cycle._timer_revision)
        assert shown == [1]
        clock.return_value = 130.0
        cycle._tick(cycle._timer_revision)
        assert shown == [1, 3]
    finally:
        cycle.close()


def test_new_output_after_dwell_displays_immediately(clock: Mock) -> None:
    present = Mock()
    cycle = StoryOutputDisplayCycle(present, Mock(), 5)
    cycle.submit("first", [], 1)
    clock.return_value = 108.0
    cycle.submit("second", [], 2)
    assert [call.args[0].narration for call in present.call_args_list] == ["first", "second"]
    cycle.close()


def test_zero_dwell_displays_all_completed_outputs(clock: Mock) -> None:
    present = Mock()
    cycle = StoryOutputDisplayCycle(present, Mock(), 0)
    for position in range(1, 4):
        cycle.submit(str(position), [], position)
    assert [call.args[0].sequence for call in present.call_args_list] == [1, 2, 3]
    cycle.close()


def test_restored_waiting_slot_preserves_order_and_presentation(clock: Mock) -> None:
    present = Mock()
    cycle = StoryOutputDisplayCycle(present, Mock(), 10)
    cycle.submit("first", [], 1)
    cycle.submit("second", [], 2)
    cycle.submit("third", [], 3)
    checkpoint = OutputCheckpoint.model_validate(cycle.snapshot())
    cycle.close()
    restored = StoryOutputDisplayCycle(present, Mock(), 10)
    restored.restore(checkpoint)
    restored.start()
    restored.submit("late second", [], 2)
    assert present.call_count == 1
    assert restored.flush()
    assert [call.args[0].sequence for call in present.call_args_list] == [1, 3]
    assert restored.snapshot()["pending"] is None
    restored.close()


def test_in_flight_presentation_does_not_block_intake_or_allow_overlap(clock: Mock) -> None:
    started, release = Event(), Event()
    shown: list[int] = []

    def present(output: StoryOutput) -> None:
        if output.sequence == 1:
            started.set()
            assert release.wait(5)
        shown.append(output.sequence)

    cycle = StoryOutputDisplayCycle(present, Mock(), 0)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(cycle.submit, "first", [], 1)
            assert started.wait(5)
            cycle.submit("second", [], 2)
            cycle.submit("third", [], 3)
            assert shown == []
            # A crash during preparation retains an accepted, unshown output.
            assert cycle.snapshot()["pending"]["sequence"] == 3
            release.set()
            first.result(timeout=5)
        assert shown == [1, 3]
    finally:
        release.set()
        cycle.close()


def test_failed_presentation_releases_gate_for_recovery(clock: Mock) -> None:
    present = Mock(side_effect=OSError("canvas unavailable"))
    cycle = StoryOutputDisplayCycle(present, Mock(), 0)
    with pytest.raises(OSError, match="canvas unavailable"):
        cycle.submit("first", [], 1)
    assert cycle.snapshot()["pending"]["sequence"] == 1
    assert not cycle.payload()["active"]
    present.side_effect = None
    cycle.submit("first", [], 1)
    assert cycle.payload()["displayed_sequence"] == 1
    cycle.submit("second", [], 2)
    assert cycle.payload()["displayed_sequence"] == 2
    cycle.close()


def test_failed_checkpoint_can_be_retried_without_replaying_scene(clock: Mock) -> None:
    present, changed = Mock(), Mock(side_effect=OSError("disk unavailable"))
    cycle = StoryOutputDisplayCycle(present, changed, 0)
    with pytest.raises(OSError, match="disk unavailable"):
        cycle.submit("first", [], 1)
    assert not cycle.payload()["active"]
    changed.side_effect = None
    cycle.submit("first", [], 1)
    assert present.call_count == 1
    assert changed.call_count == 2
    cycle.submit("second", [], 2)
    assert cycle.payload()["displayed_sequence"] == 2
    cycle.close()


def test_close_invalidates_callbacks_and_retains_pending_checkpoint(clock: Mock) -> None:
    present = Mock()
    cycle = StoryOutputDisplayCycle(present, Mock(), 10)
    cycle.submit("first", [], 1)
    cycle.submit("second", [], 2)
    revision = cycle._timer_revision
    cycle.close()
    clock.return_value = 200.0
    cycle._tick(revision)
    assert present.call_count == 1
    assert cycle.snapshot()["pending"]["sequence"] == 2
    with pytest.raises(RuntimeError, match="closed"):
        cycle.submit("late", [], 3)


def test_timer_advances_without_input_or_explicit_flush() -> None:
    displayed = Event()

    def present(output: StoryOutput) -> None:
        if output.sequence == 2:
            displayed.set()

    cycle = StoryOutputDisplayCycle(present, Mock(), 0.01)
    try:
        cycle.submit("first", [], 1)
        cycle.submit("second", [], 2)
        assert displayed.wait(5)
        assert cycle.flush()
        assert cycle.payload()["displayed_sequence"] == 2
    finally:
        cycle.close()
