"""Concurrency and failure boundaries of the canvas story intake."""

from concurrent.futures import ThreadPoolExecutor
from asyncio import CancelledError
from threading import Event
from unittest.mock import Mock

import pytest

from pydantic import JsonValue

from components.story.cycle import StoryCycle, StoryResult, story_cycle_length


@pytest.mark.parametrize(("config", "expected"), [
    ({}, 15.0),
    ({"cycle_length": 2, "cooldown_duration": 10}, 2.0),
    ({"cycle_length": None, "cooldown_duration": 0}, 0.0),
    ({"action_cooldown_base_seconds": 7}, 7.0),
    ({"cycle_length": -1}, 0.0),
])
def test_cycle_timing_preserves_legacy_configuration(config: dict[str, JsonValue], expected: float) -> None:
    assert story_cycle_length(config) == expected


def test_collection_window_preserves_every_input_and_deduplicates_ids() -> None:
    cycle = StoryCycle(60)
    calls: list[list[str]] = []

    def consume(ids: list[str]) -> dict[str, StoryResult]:
        calls.append(ids)
        return {turn_id: {"status": "completed"} for turn_id in ids}

    cycle.bind(consume)
    try:
        first = cycle.submit("speech")
        assert cycle.submit("speech") is first
        second = cycle.submit("game")
        third = cycle.submit("more-speech")
        assert calls == []
        assert cycle.payload()["pending"] == 3
        assert cycle.wait(5)
        assert calls == [["speech", "game", "more-speech"]]
        assert all(future.result()["status"] == "completed" for future in (first, second, third))
        assert not cycle.is_active
    finally:
        cycle.close()


def test_input_during_execution_waits_for_one_subsequent_batch() -> None:
    cycle = StoryCycle(60)
    started, release = Event(), Event()
    calls: list[list[str]] = []

    def consume(ids: list[str]) -> dict[str, StoryResult]:
        calls.append(ids)
        if ids == ["first"]:
            started.set()
            assert release.wait(5)
        return {turn_id: {"status": "completed"} for turn_id in ids}

    cycle.bind(consume)
    try:
        cycle.submit("first")
        cycle.flush()
        assert started.wait(5)
        cycle.submit("speech")
        cycle.submit("game")
        cycle.flush()
        assert calls == [["first"]]
        release.set()
        assert cycle.wait(5)
        assert calls == [["first"], ["speech", "game"]]
    finally:
        release.set()
        cycle.close()


@pytest.mark.parametrize("failure", [RuntimeError("journal unavailable"), CancelledError("cancelled")])
def test_consumer_failure_releases_gate_and_settles_all_futures(failure: BaseException) -> None:
    cycle = StoryCycle(60)

    def fail(ids: list[str]) -> dict[str, StoryResult]:
        if "bad" in ids:
            raise failure
        return {turn_id: {"status": "completed"} for turn_id in ids}

    cycle.bind(fail)
    try:
        futures = [cycle.submit("bad"), cycle.submit("also-bad")]
        assert cycle.wait(5)
        assert not cycle.is_active
        for future in futures:
            with pytest.raises(type(failure), match=str(failure)):
                future.result()
        recovered = cycle.submit("recovered")
        assert cycle.wait(5)
        assert recovered.result() == {"status": "completed"}
    finally:
        cycle.close()


def test_timer_releases_window_without_explicit_drain() -> None:
    cycle = StoryCycle(0.01)
    consume = Mock(return_value={"input": {"status": "completed"}})
    cycle.bind(consume)
    try:
        assert cycle.submit("input").result(timeout=5) == {"status": "completed"}
        consume.assert_called_once_with(["input"])
    finally:
        cycle.close()


def test_shutdown_drains_queued_input_after_active_consumer() -> None:
    cycle = StoryCycle(0)
    started, release = Event(), Event()

    def consume(ids: list[str]) -> dict[str, StoryResult]:
        if ids == ["first"]:
            started.set()
            assert release.wait(5)
        return {turn_id: {"status": "completed"} for turn_id in ids}

    cycle.bind(consume)
    try:
        first = cycle.submit("first")
        assert started.wait(5)
        second = cycle.submit("second")
        with ThreadPoolExecutor(max_workers=1) as pool:
            closing = pool.submit(cycle.close)
            release.set()
            closing.result(timeout=5)
        assert first.result()["status"] == second.result()["status"] == "completed"
        with pytest.raises(RuntimeError, match="closed"):
            cycle.submit("late")
    finally:
        release.set()
        cycle.close()


def test_shutdown_cancels_inputs_when_no_engine_was_bound() -> None:
    cycle = StoryCycle(60)
    future = cycle.submit("unbound")
    cycle.close()
    assert future.cancelled()
    assert not cycle.is_active
