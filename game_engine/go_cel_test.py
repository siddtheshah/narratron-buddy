"""Offline worker compatibility, complete-operation parity and deadline probes."""
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
from threading import Barrier
from unittest.mock import Mock, patch

from pydantic import JsonValue
import pytest

from game_engine.game_runtime import FunctionDefinition, GameAction, GameProgram, GameStep, GameWorkBudgetExceeded
from testing.game_fixtures import counter_definition, exchange_definition
from testlab.game_benchmark import sample
from game_engine.go_cel import DEFAULT_COST_LIMIT, GoGameState, GoWorker, GoWorkerPool


@pytest.fixture
def worker() -> Iterator[GoWorker]:
    default = "scratch/cel-go-worker.exe" if sys.platform == "win32" else "scratch/cel-go-worker"
    executable = Path(os.environ.get("NARRATRON_CEL_GO_WORKER", default))
    if not executable.is_file():
        pytest.skip("Build game_engine/cel_go_worker and set NARRATRON_CEL_GO_WORKER")
    instance = GoWorker(executable)
    instance.start()
    try:
        yield instance
    finally:
        instance.close()


def expression(worker: GoWorker, source: str, context: dict[str, JsonValue]) -> JsonValue:
    return worker.request({"op": "eval", "source": source, "context": context})


@pytest.mark.parametrize("source,context", [
    ("value == null", {"value": None}),
    ("null != value", {"value": {"kind": "wrap"}}),
    ("value != null", {"value": [None]}),
    ("false && missing != null", {}),
    ("true || null == missing", {}),
    ("missing != null && false", {}),
    ("missing == null || true", {}),
    ("1 / 0 == 2 || true", {}),
    ('[{"kind": "wrap"}, null].filter(r, r != null).size() == 1', {}),
    ("-7 / 2", {}), ("-7 % 2", {}),
    ("9223372036854775807", {}),
    ("value", {"value": 9223372036854775807}),
    ("value", {"value": -9223372036854775808}),
    ("[1, 2, 3].map(x, twice(x))", {}),
    ('identity({"value": [null, true, 2, "text"]})', {}),
    ("random_int(4, 4)", {}),
    ("random_int(identity(1u), 1)", {}),
    ("18446744073709551615u", {}),
    ('twice("bad") == 1 || true', {}),
    ("wrapped()", {}),
    ('constantScope({"answer": 1})', {}),
    ('int("3") + int(true) + int(false)', {}),
])
def test_go_results_match_python(worker: GoWorker, source: str, context: dict[str, JsonValue]) -> None:
    definition = counter_definition()
    definition.functions["twice"] = FunctionDefinition(parameters=["x"], expr="x * 2")
    definition.functions["identity"] = FunctionDefinition(parameters=["x"], expr="x")
    definition.functions["roll"] = FunctionDefinition(parameters=[], expr="random_int(4, 4)")
    definition.functions["wrapped"] = FunctionDefinition(parameters=[], expr="roll()")
    definition.constants["answer"] = 9
    definition.functions["constantScope"] = FunctionDefinition(parameters=["constants"], expr="constants.answer")
    definition.transitions["clear"].result = source
    program = GameProgram(definition)
    worker.load(program)
    assert expression(worker, source, context) == program.execution().evaluate(source, context)


@pytest.mark.parametrize("source", [
    "missing == null", "null != missing", "9223372036854775807 + 1", "1 / 0",
    "random_int(true, 6)", "random_int(6, 1)", "random_int(1u, 6u)",
    "twice(18446744073709551615u)",
    "twice()", "twice(1, 2)", "[missing]", '{"x": missing}', "[1, 2].map(x, 1 / 0)",
])
def test_go_rejects_invalid_values(worker: GoWorker, source: str) -> None:
    definition = counter_definition()
    definition.functions["twice"] = FunctionDefinition(parameters=["x"], expr="x * 2")
    definition.transitions["clear"].result = source
    program = GameProgram(definition)
    worker.load(program)
    with pytest.raises(ValueError):
        expression(worker, source, {})


def test_go_random_helpers_bypass_caches(worker: GoWorker) -> None:
    definition = counter_definition()
    definition.functions["roll"] = FunctionDefinition(parameters=[], expr="random_int(1, 1000000000)")
    definition.functions["wrapped"] = FunctionDefinition(parameters=[], expr="roll()")
    source = "[1, 2, 3].map(x, wrapped())"
    definition.transitions["clear"].result = source
    worker.load(GameProgram(definition))
    # Check dispatch/cache counters rather than asserting probabilistic values.
    expression(worker, source, {})
    assert worker.last_metrics["helper_calls"] == 6
    assert worker.last_metrics["cache_hits"] == 0
    expression(worker, source, {})
    assert worker.last_metrics["helper_calls"] == 6


def test_go_rejects_oversized_results(worker: GoWorker) -> None:
    definition = counter_definition()
    source = "values.map(x, text)"
    definition.transitions["clear"].result = source
    worker.load(GameProgram(definition))
    with pytest.raises(ValueError, match="size budget"):
        expression(worker, source, {"values": list(range(20)), "text": "x" * 10000})


def test_go_preserves_integral_double_input_and_records_string_difference(worker: GoWorker) -> None:
    definition = counter_definition()
    source = "random_int(value, 4)"
    definition.transitions["clear"].result = source
    definition.transitions["add"].guard = "string(value)"
    program = GameProgram(definition)
    worker.load(program)
    with pytest.raises(ValueError):
        expression(worker, source, {"value": 2.0})
    with pytest.raises(ValueError):
        program.execution().evaluate(source, {"value": 2.0})
    assert expression(worker, "string(value)", {"value": 2.0}) == "2"
    assert program.execution().evaluate("string(value)", {"value": 2.0}) == "2.0"


def test_go_counter_and_exchange_operations_match(worker: GoWorker, tmp_path: Path) -> None:
    from game_engine.game_state import GameState

    for definition, actions in [
        (counter_definition(), [("add", {"amount": 2}, "player"), ("clear", {}, "player")]),
        (exchange_definition(), [("player_place", {"item": "red"}, "player"), ("narratron_place", {"item": "green"}, "narratron"), ("player_finalize", {}, "player"), ("narratron_finalize", {}, "narratron")]),
    ]:
        directory = tmp_path / "game"
        directory.mkdir(exist_ok=True)
        (directory / "game.json").write_text(definition.model_dump_json(), encoding="utf-8")
        expected = GameState(tmp_path, Mock(), Mock(), backend="python")
        actual = GoGameState(tmp_path, Mock(), Mock(), worker=worker)
        assert actual.payload() == expected.payload()
        for name, arguments, actor in actions:
            action = GameAction(name=name, arguments=arguments, revision=expected.revision)
            assert actual.act(action, actor) == expected.act(action, actor)
            assert actual.serialize() == expected.serialize()
            assert actual.story_snapshot("narratron") == expected.story_snapshot("narratron")


def test_go_checkers_batches_draft_and_preserves_payloads(worker: GoWorker) -> None:
    from game_engine.game_state import GameState

    def python_factory(directory: Path, persist: Callable[[], None], notify: Callable[..., None]) -> GameState:
        return GameState(directory, persist, notify, backend="python")

    expected_times, expected = sample(Path("adventures/example_adventure"), python_factory)

    def factory(directory: Path, persist: Callable[[], None], notify: Callable[..., None]) -> GoGameState:
        return GoGameState(directory, persist, notify, worker=worker)

    actual_times, actual = sample(Path("adventures/example_adventure"), factory)
    assert actual == expected
    assert actual_times.keys() == expected_times.keys()
    assert worker.requests == 5


def test_go_timeout_kills_worker_rolls_back_and_can_replace(worker: GoWorker, tmp_path: Path) -> None:
    definition = counter_definition()
    definition.constants["values"] = list(range(3000))
    definition.invariants = ["state.count == 0 || constants.values.all(x, constants.values.all(y, x + y >= 0))"]
    directory = tmp_path / "game"
    directory.mkdir()
    (directory / "game.json").write_text(definition.model_dump_json(), encoding="utf-8")
    persist = Mock()
    worker.cost_limit = 100_000_000
    game = GoGameState(tmp_path, persist, Mock(), worker=worker)
    assert game.program is not None
    previous = game.serialize()
    old_process = worker.process
    assert old_process is not None
    before = worker.requests
    worker.timeout = 0.02
    with patch("game_engine.game_state.logger.exception"), pytest.raises(GameWorkBudgetExceeded):
        game.act(GameAction(name="add", arguments={"amount": 1}, revision=0))
    assert game.serialize() == previous
    persist.assert_not_called()
    assert worker.requests == before + 1
    assert old_process.poll() is not None
    assert worker.process is None
    worker.timeout = 5
    worker.cost_limit = DEFAULT_COST_LIMIT
    worker.start()
    worker.load(GameProgram(counter_definition()))
    assert expression(worker, "initial", {"initial": {"count": 0}}) == {"count": 0}


def test_go_cost_limit_aborts_expensive_expression_and_keeps_worker_alive(worker: GoWorker, tmp_path: Path) -> None:
    definition = counter_definition()
    definition.constants["values"] = list(range(500))
    definition.invariants = ["state.count == 0 || constants.values.all(x, constants.values.all(y, x + y >= 0))"]
    directory = tmp_path / "game"
    directory.mkdir()
    (directory / "game.json").write_text(definition.model_dump_json(), encoding="utf-8")
    persist = Mock()
    worker.cost_limit = 20_000
    game = GoGameState(tmp_path, persist, Mock(), worker=worker)
    previous = game.serialize()
    old_process = worker.process
    assert old_process is not None
    with patch("game_engine.game_state.logger.exception"), pytest.raises(GameWorkBudgetExceeded, match="cost limit"):
        game.act(GameAction(name="add", arguments={"amount": 1}, revision=0))
    assert game.serialize() == previous
    persist.assert_not_called()
    assert worker.process is old_process
    assert old_process.poll() is None
    assert worker.last_metrics["cost"] > 20_000
    worker.cost_limit = DEFAULT_COST_LIMIT
    assert game.act(GameAction(name="clear", revision=0))["revision"] == 1
    assert game.revision == 1


def test_go_cost_limit_aborts_memory_amplification_bombs(worker: GoWorker) -> None:
    definition = counter_definition()
    string_bomb = "values.map(x, text + text + text + text).size() > 0"
    list_bomb = "values.map(x, items + items + items + items).size() > 0"
    definition.transitions["clear"].guard = string_bomb
    definition.transitions["add"].guard = list_bomb
    worker.load(GameProgram(definition))
    worker.cost_limit = 5_000
    try:
        with pytest.raises(GameWorkBudgetExceeded, match="cost limit"):
            expression(
                worker,
                string_bomb,
                {"values": list(range(200)), "text": "a" * 4096},
            )
        with pytest.raises(GameWorkBudgetExceeded, match="cost limit"):
            expression(
                worker,
                list_bomb,
                {"values": list(range(200)), "items": list(range(500))},
            )
    finally:
        worker.cost_limit = DEFAULT_COST_LIMIT
    assert worker.process is not None
    assert worker.process.poll() is None
    assert expression(worker, "initial", {"initial": {"count": 0}}) == {"count": 0}


def test_go_masked_helper_cost_exhaustion_is_not_suppressed(worker: GoWorker) -> None:
    definition = counter_definition()
    definition.constants["values"] = list(range(300))
    definition.functions["burn"] = FunctionDefinition(
        parameters=[],
        expr="constants.values.all(x, constants.values.all(y, x + y >= 0))",
    )
    or_masked = "burn() || true"
    and_masked = "burn() && false"
    definition.transitions["clear"].guard = or_masked
    definition.transitions["add"].guard = and_masked
    worker.cost_limit = 10_000
    worker.load(GameProgram(definition))
    try:
        with pytest.raises(GameWorkBudgetExceeded, match="cost limit"):
            expression(worker, or_masked, {})
        with pytest.raises(GameWorkBudgetExceeded, match="cost limit"):
            expression(worker, and_masked, {})
    finally:
        worker.cost_limit = DEFAULT_COST_LIMIT
    assert worker.process is not None
    assert worker.process.poll() is None


def test_go_parent_deadline_covers_stalled_stdio(worker: GoWorker) -> None:
    # Independently exercise forced termination, without relying on CEL's
    # cooperative cancellation or allocating an adversarial native container.
    old_process = worker.process
    assert old_process is not None
    worker.timeout = 0.02

    def stalled_exchange(frame: bytes) -> bytes:
        old_process.wait()
        return b""

    with patch("game_engine.go_cel.GoWorker._exchange", side_effect=stalled_exchange):
        with pytest.raises(GameWorkBudgetExceeded):
            worker.request({"op": "ping"})
    assert old_process.poll() is not None
    assert worker.process is None


def test_go_parent_rejects_invalid_input_before_rpc(worker: GoWorker, tmp_path: Path) -> None:
    directory = tmp_path / "game"
    directory.mkdir()
    (directory / "game.json").write_text(counter_definition().model_dump_json(), encoding="utf-8")
    persist = Mock()
    game = GoGameState(tmp_path, persist, Mock(), worker=worker)
    before = worker.requests
    with patch("game_engine.game_state.logger.exception"), pytest.raises(ValueError, match="Invalid input"):
        game.act(GameAction(name="add", arguments={"amount": 0}, revision=0))
    assert worker.requests == before
    assert game.state == {"count": 0}
    persist.assert_not_called()


def test_go_parent_validates_draft_candidate_schema_before_commit(worker: GoWorker, tmp_path: Path) -> None:
    definition = counter_definition()
    definition.local_preview = True
    definition.invariants = []
    definition.transitions["add"].triggering = False
    definition.transitions["add"].result = '{"count": 99}'
    directory = tmp_path / "game"
    directory.mkdir()
    (directory / "game.json").write_text(definition.model_dump_json(), encoding="utf-8")
    persist = Mock()
    game = GoGameState(tmp_path, persist, Mock(), worker=worker)
    previous = game.serialize()
    with patch("game_engine.game_state.logger.exception"), pytest.raises(ValueError):
        game.act(GameAction(name="clear", revision=0, draft=[GameStep(name="add", arguments={"amount": 1})]))
    assert game.serialize() == previous
    persist.assert_not_called()


def test_pool_runs_workers_concurrently_and_bounds_wait(worker: GoWorker) -> None:
    pool = GoWorkerPool(worker.executable, size=2)
    barrier = Barrier(2)

    def ping(index: int) -> int:
        with pool.borrow() as borrowed:
            borrowed.ensure_started()
            barrier.wait(timeout=5)
            borrowed.request({"op": "ping"})
            assert borrowed.process is not None
            return borrowed.process.pid

    try:
        with ThreadPoolExecutor(max_workers=2) as threads:
            assert len(set(threads.map(ping, range(2)))) == 2
        pool.timeout = 0.02
        with pool.borrow(), pool.borrow():
            with pytest.raises(GameWorkBudgetExceeded, match="waiting for a worker"):
                with pool.borrow():
                    pytest.fail("exhausted pool lent a worker")
        with pool.borrow() as borrowed:
            borrowed.close()
            borrowed.ensure_started()
            assert borrowed.request({"op": "ping"}) is not None
    finally:
        pool.close()


def test_game_operations_move_between_pool_workers(worker: GoWorker, tmp_path: Path) -> None:
    pool = GoWorkerPool(worker.executable, size=2)
    directory = tmp_path / "game"
    directory.mkdir()
    (directory / "game.json").write_text(counter_definition().model_dump_json(), encoding="utf-8")
    try:
        with patch("game_engine.go_cel.shared_go_worker_pool", return_value=pool):
            game = GoGameState(tmp_path, Mock(), Mock())
            first = game.worker
            game.act(GameAction(name="add", arguments={"amount": 1}, revision=0))
            assert game.worker is not first
            game.act(GameAction(name="add", arguments={"amount": 1}, revision=1))
            assert game.worker is first
            assert game.state == {"count": 2}
            assert all(game._package_key in borrowed.loaded_packages for borrowed in pool.workers)
            assert game.worker is not None
            game.worker.close()
            game.act(GameAction(name="clear", revision=2))
            game.act(GameAction(name="add", arguments={"amount": 1}, revision=3))
            assert game.state == {"count": 1}
    finally:
        pool.close()


def test_candidate_output_budget_stops_operation_and_preserves_worker(worker: GoWorker) -> None:
    definition = counter_definition()
    definition.initial = {"count": 0, "text": "x" * 100000}
    definition.state_schema = {"type": "object"}
    definition.invariants = []
    definition.view.props = {}
    definition.transitions["clear"].result = "state"
    worker.load(GameProgram(definition))
    with pytest.raises(ValueError, match="aggregate output size budget"):
        worker.request({
            "op": "act", "state": definition.initial,
            "steps": [{"name": "clear", "arguments": {}} for _ in range(65)],
        })
    assert worker.request({"op": "ping"}) is not None
