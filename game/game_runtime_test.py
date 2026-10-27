"""Random CEL calls must draw afresh, including through cached helper expressions."""
from functools import partial
from unittest.mock import Mock, patch

import pytest
from pydantic import JsonValue

from game.game_runtime import (
    Budget, FunctionDefinition, GameProgram, GameWorkBudgetExceeded,
    MAX_STEPS, MAX_SECONDS, RECOVERY_MAX_STEPS, RECOVERY_MAX_SECONDS,
    evaluate_with_recovery, validate_game_package,
)
from testing.game_fixtures import counter_definition


@pytest.mark.parametrize("value", [None, {}, {"kind": "wrap"}, [], [None], "white", 0, False])
@pytest.mark.parametrize("expression", ["value == null", "null == value", "value != null", "null != value"])
def test_optional_json_entities_compare_to_null(value: JsonValue, expression: str) -> None:
    definition = counter_definition()
    definition.transitions["clear"].result = expression
    execution = GameProgram(definition).execution()
    expected = value is None if "==" in expression else value is not None
    assert execution.evaluate(expression, {"value": value}) == expected


@pytest.mark.parametrize("expression", ["missing == null", "null == missing", "missing != null", "null != missing"])
def test_null_comparison_preserves_missing_value_errors(expression: str) -> None:
    definition = counter_definition()
    definition.transitions["clear"].result = expression
    execution = GameProgram(definition).execution()
    with pytest.raises(ValueError, match="CEL evaluation failed"):
        execution.evaluate(expression, {})


@pytest.mark.parametrize("expression,expected", [
    ("false && missing != null", False),
    ("true || null == missing", True),
    ('[{"kind": "wrap"}, null].filter(r, r != null).size() == 1', True),
])
def test_nullable_entities_preserve_cel_error_masking(expression: str, expected: bool) -> None:
    definition = counter_definition()
    definition.transitions["clear"].result = expression
    execution = GameProgram(definition).execution()
    assert execution.evaluate(expression, {}) == expected


@pytest.mark.parametrize('lower, upper', [
    (1, 6), (-6, -1), (-3, 3), (0, 0), (7, 7),
    (-9223372036854775808, 9223372036854775807),
])
def test_random_int_returns_inclusive_bounded_integers(lower: int, upper: int) -> None:
    definition = counter_definition()
    source = f'random_int({lower}, {upper})'
    definition.transitions['clear'].result = source
    execution = GameProgram(definition).execution()
    for _ in range(20):
        value = execution.evaluate(source, {})
        assert type(value) is int
        assert lower <= value <= upper


@pytest.mark.parametrize('source', [
    'random_int()', 'random_int(1)', 'random_int(1, 2, 3)',
    'random_int(6, 1)', 'random_int(true, 6)', 'random_int(1, false)',
    'random_int(1.0, 6)', 'random_int(1, 6.0)', 'random_int("1", 6)',
    'random_int(1, null)', 'random_int([], 6)', 'random_int(1u, 6u)',
])
def test_random_int_rejects_invalid_arguments(source: str) -> None:
    definition = counter_definition()
    definition.transitions['clear'].result = source
    execution = GameProgram(definition).execution()
    with pytest.raises(ValueError, match='CEL evaluation failed'):
        execution.evaluate(source, {})


@pytest.mark.parametrize('source', [
    'random_int(1, 6)', 'roll(6)', 'wrappedRoll()',
])
def test_random_calls_bypass_expression_cache_transitively(source: str) -> None:
    definition = counter_definition()
    definition.functions = {
        'roll': FunctionDefinition(parameters=['sides'], expr='random_int(1, sides)'),
        'wrappedRoll': FunctionDefinition(parameters=[], expr='roll(6)'),
    }
    definition.transitions['clear'].result = source
    execution = GameProgram(definition).execution()
    with patch('game.game_runtime.random.randint', side_effect=[1, 6]) as draw:
        assert execution.evaluate(source, {}) == 1
        assert execution.evaluate(source, {}) == 6
    assert draw.call_args_list[0].args == (1, 6)
    assert draw.call_args_list[1].args == (1, 6)


def test_random_helpers_draw_for_each_macro_iteration() -> None:
    definition = counter_definition()
    definition.functions['roll'] = FunctionDefinition(parameters=[], expr='random_int(1, 6)')
    source = '[1, 2, 3].map(x, roll())'
    definition.transitions['clear'].result = source
    execution = GameProgram(definition).execution()
    with patch('game.game_runtime.random.randint', side_effect=[1, 3, 6]):
        assert execution.evaluate(source, {}) == [1, 3, 6]


def test_random_int_builtin_cannot_be_redefined() -> None:
    definition = counter_definition()
    definition.functions['random_int'] = FunctionDefinition(parameters=[], expr='1')
    with pytest.raises(ValueError, match='duplicate function name'):
        GameProgram(definition)


def test_step_exhaustion_remains_latched() -> None:
    budget = Budget(remaining=0)
    with pytest.raises(GameWorkBudgetExceeded, match="step limit"):
        budget.charge()
    budget.remaining = MAX_STEPS
    with pytest.raises(GameWorkBudgetExceeded, match="step limit"):
        budget.check()


def test_deadline_exhaustion_remains_latched() -> None:
    budget = Budget(remaining=257)
    with patch("game.game_runtime.monotonic", return_value=budget.deadline + 1):
        with pytest.raises(GameWorkBudgetExceeded, match="time limit"):
            budget.charge()
    with pytest.raises(GameWorkBudgetExceeded, match="time limit"):
        budget.check()


@pytest.mark.parametrize("source", ["true || burn()", "false && burn()"])
def test_masked_helper_exhaustion_preserves_recoverable_error(source: str) -> None:
    definition = counter_definition()
    definition.constants = {"values": list(range(100))}
    definition.functions["burn"] = FunctionDefinition(
        parameters=[], expr="constants.values.map(x, x + 1).size() > 0",
    )
    definition.transitions["clear"].result = source
    execution = GameProgram(definition).execution()
    execution.budget = Budget(remaining=80)
    with pytest.raises(GameWorkBudgetExceeded, match="step limit"):
        execution.evaluate(source, {})


def test_recovery_has_one_fixed_ceiling() -> None:
    budgets: list[Budget] = []

    def exhaust(budget: Budget) -> None:
        budgets.append(budget)
        budget.remaining = 0
        budget.charge()

    with pytest.raises(GameWorkBudgetExceeded):
        evaluate_with_recovery(exhaust)
    assert len(budgets) == 2
    assert [budget.seconds for budget in budgets] == [MAX_SECONDS, RECOVERY_MAX_SECONDS]


def test_only_resource_failures_are_retried() -> None:
    operation = Mock(side_effect=ValueError("invalid action"))
    with pytest.raises(ValueError, match="invalid action"):
        evaluate_with_recovery(operation)
    operation.assert_called_once()


def test_package_initial_preparation_can_recover() -> None:
    with patch("game.game_runtime.Budget", side_effect=partial(Budget, remaining=0)):
        program = validate_game_package(counter_definition().model_dump_json().encode())
    assert program.initial_view[0]["children"][0]["props"]["text"] == "Count: 0"


def test_recovery_uses_larger_step_and_time_budgets() -> None:
    limits: list[tuple[int, float]] = []

    def evaluate(budget: Budget) -> int:
        limits.append((budget.remaining, budget.seconds))
        if len(limits) == 1:
            budget.remaining = 0
            budget.charge()
        return 42

    assert evaluate_with_recovery(evaluate) == 42
    assert limits == [(MAX_STEPS, MAX_SECONDS), (RECOVERY_MAX_STEPS, RECOVERY_MAX_SECONDS)]
