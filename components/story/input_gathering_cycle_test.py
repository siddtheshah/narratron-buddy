"""Concurrency and failure boundaries of the story input gathering cycle."""

from concurrent.futures import ThreadPoolExecutor
from asyncio import CancelledError
from threading import Event
from unittest.mock import patch

import pytest

from pydantic import JsonValue

from components.story.input_gathering_cycle import StoryInputGatheringCycle, StoryResult, story_input_cycle_length


@pytest.mark.parametrize(("config", "expected"), [
    ({}, 15.0),
    ({"input_cycle_length": None}, 15.0),
    ({"input_cycle_length": 2, "output_cycle_length": 10}, 2.0),
    ({"input_cycle_length": -1}, 0.0),
    ({"cycle_length": 2}, 15.0),
    ({"cooldown_duration": 0, "action_cooldown_base_seconds": 7}, 15.0),
    ({"input_cycle_length": 3, "cycle_length": 8}, 3.0),
    ({"input_cycle_length": 0, "cycle_length": 8}, 0.0),
    ({"input_cycle_length": None, "cycle_length": 8}, 15.0),
])
def test_input_timing_uses_only_input_configuration(config: dict[str, JsonValue], expected: float) -> None:
    assert story_input_cycle_length(config) == expected


def test_inputs_accepted_before_binding_form_one_batch_and_deduplicate_ids() -> None:
    cycle = StoryInputGatheringCycle(60)
    calls: list[list[str]] = []

    def consume(ids: list[str]) -> dict[str, StoryResult]:
        calls.append(ids)
        return {turn_id: {"status": "completed"} for turn_id in ids}

    try:
        first = cycle.submit("speech")
        assert cycle.submit("speech") is first
        second = cycle.submit("game")
        third = cycle.submit("more-speech")
        assert calls == []
        assert cycle.payload()["pending"] == 3
        cycle.bind(consume)
        assert all(future.result(timeout=5)["status"] == "completed" for future in (first, second, third))
        assert cycle.wait(5)
        assert calls == [["speech", "game", "more-speech"]]
        assert not cycle.is_active
    finally:
        cycle.close()


def test_input_gathering_appends_waiting_inputs_until_active_batch_and_interval_finish() -> None:
    cycle = StoryInputGatheringCycle(60)
    started, release = Event(), Event()
    calls: list[list[str]] = []

    def consume(ids: list[str]) -> dict[str, StoryResult]:
        calls.append(ids)
        if ids == ["first"]:
            started.set()
            assert release.wait(5)
        return {turn_id: {"status": "completed"} for turn_id in ids}

    with patch("components.story.input_gathering_cycle.time.monotonic", return_value=100.0) as now, patch(
        "components.story.input_gathering_cycle.Timer",
    ) as timer:
        cycle.bind(consume)
        try:
            cycle.submit("first")
            assert started.wait(5)
            now.return_value = 110.0
            second = cycle.submit("speech")
            now.return_value = 170.0
            third = cycle.submit("game")
            assert cycle.submit("game") is third
            assert calls == [["first"]]
            assert cycle.payload()["pending"] == 2
            release.set()
            assert second.result(timeout=5)["status"] == "completed"
            assert third.result(timeout=5)["status"] == "completed"
            assert calls == [["first"], ["speech", "game"]]
            timer.assert_not_called()
        finally:
            release.set()
            cycle.close()


def test_first_and_post_cooldown_inputs_start_immediately() -> None:
    cycle = StoryInputGatheringCycle(60)

    def consume(ids: list[str]) -> dict[str, StoryResult]:
        return {turn_id: {"status": "completed"} for turn_id in ids}

    with patch("components.story.input_gathering_cycle.time.monotonic", return_value=100.0) as now, patch(
        "components.story.input_gathering_cycle.Timer",
    ) as timer:
        cycle.bind(consume)
        try:
            assert cycle.submit("first").result(timeout=5)["status"] == "completed"
            assert cycle.wait(5)
            now.return_value = 200.0
            assert cycle.submit("later").result(timeout=5)["status"] == "completed"
            timer.assert_not_called()
        finally:
            cycle.close()


def test_short_batch_waits_for_original_interval_without_extending_it() -> None:
    cycle = StoryInputGatheringCycle(60)
    calls: list[list[str]] = []

    def consume(ids: list[str]) -> dict[str, StoryResult]:
        calls.append(ids)
        return {turn_id: {"status": "completed"} for turn_id in ids}

    with patch("components.story.input_gathering_cycle.time.monotonic", return_value=100.0) as now, patch(
        "components.story.input_gathering_cycle.Timer",
    ) as timer:
        cycle.bind(consume)
        try:
            assert cycle.submit("first").result(timeout=5)["status"] == "completed"
            assert cycle.wait(5)
            cycle.flush()
            now.return_value = 110.0
            second = cycle.submit("second")
            now.return_value = 150.0
            third = cycle.submit("third")
            assert not second.done() and not third.done()
            assert calls == [["first"]]
            assert timer.call_args.args[0] == 10.0
            now.return_value = 160.0
            cycle._tick()
            assert second.result(timeout=5)["status"] == "completed"
            assert third.result(timeout=5)["status"] == "completed"
            assert calls == [["first"], ["second", "third"]]
        finally:
            cycle.close()


@pytest.mark.parametrize("failure", [RuntimeError("journal unavailable"), CancelledError("cancelled")])
def test_consumer_failure_releases_gate_and_settles_all_futures(failure: BaseException) -> None:
    cycle = StoryInputGatheringCycle(60)

    def fail(ids: list[str]) -> dict[str, StoryResult]:
        if "bad" in ids:
            raise failure
        return {turn_id: {"status": "completed"} for turn_id in ids}

    try:
        futures = [cycle.submit("bad"), cycle.submit("also-bad")]
        cycle.bind(fail)
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


def test_timer_releases_followup_without_explicit_drain() -> None:
    cycle = StoryInputGatheringCycle(0.01)

    def consume(ids: list[str]) -> dict[str, StoryResult]:
        return {turn_id: {"status": "completed"} for turn_id in ids}

    cycle.bind(consume)
    try:
        assert cycle.submit("first").result(timeout=5) == {"status": "completed"}
        assert cycle.submit("followup").result(timeout=5) == {"status": "completed"}
    finally:
        cycle.close()


def test_shutdown_drains_queued_input_after_active_consumer() -> None:
    cycle = StoryInputGatheringCycle(60)
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
    cycle = StoryInputGatheringCycle(60)
    future = cycle.submit("unbound")
    cycle.close()
    assert future.cancelled()
    assert not cycle.is_active
