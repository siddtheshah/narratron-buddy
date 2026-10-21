"""Random CEL calls must draw afresh, including through cached helper expressions."""
from unittest.mock import patch

import pytest

from components.canvas.game_runtime import FunctionDefinition, GameProgram
from testing.game_fixtures import counter_definition


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
    with patch('components.canvas.game_runtime.random.randint', side_effect=[1, 6]) as draw:
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
    with patch('components.canvas.game_runtime.random.randint', side_effect=[1, 3, 6]):
        assert execution.evaluate(source, {}) == [1, 3, 6]


def test_random_int_builtin_cannot_be_redefined() -> None:
    definition = counter_definition()
    definition.functions['random_int'] = FunctionDefinition(parameters=[], expr='1')
    with pytest.raises(ValueError, match='duplicate function name'):
        GameProgram(definition)
