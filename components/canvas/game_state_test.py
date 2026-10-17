"""Game-agnostic contract tests: the same runtime also runs a counter."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from components.canvas.game_runtime import GameDefinition, GameProgram
from components.canvas.game_state import GameAction, GameState, StaleGameAction


from testing.game_fixtures import counter_definition, make_game


def test_generic_state_transitions_rendering_and_restore(tmp_path: Path) -> None:
    game = make_game(tmp_path)
    assert game.error == ''
    result = game.act(GameAction(name='add', arguments={'amount': 3}, revision=0))
    assert game.state == {'count': 3}
    assert result['revision'] == 1
    assert result['view'][0]['children'][0]['props']['text'] == 'Count: 3'
    assert 'board' not in json.dumps(result)
    restored = make_game(tmp_path)
    restored.load(game.serialize()['game_state'])
    assert restored.payload() == result
    restored.act(GameAction(name='clear', revision=1))
    assert restored.state == {'count': 0}
    assert restored.revision == 2


@pytest.mark.parametrize('action', [
    GameAction(name='unknown', revision=0),
    GameAction(name='add', arguments={'amount': 11}, revision=0),
    GameAction(name='add', arguments={'amount': '1'}, revision=0),
    GameAction(name='add', arguments={'amount': 1, 'extra': 2}, revision=0),
])
def test_invalid_actions_never_mutate(tmp_path: Path, action: GameAction) -> None:
    game = make_game(tmp_path)
    before = game.serialize()
    with pytest.raises(ValueError):
        game.act(action)
    assert game.serialize() == before


def test_stale_and_concurrent_submissions_commit_once(tmp_path: Path) -> None:
    game = make_game(tmp_path)

    def attempt() -> bool:
        try:
            game.act(GameAction(name='add', arguments={'amount': 1}, revision=0))
            return True
        except StaleGameAction:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        assert [future.result() for future in futures].count(True) == 1
    assert game.state == {'count': 1}


def test_guard_schema_and_invariant_rejections_roll_back(tmp_path: Path) -> None:
    game = make_game(tmp_path)
    game.act(GameAction(name='add', arguments={'amount': 9}, revision=0))
    with pytest.raises(ValueError, match='not permitted'):
        game.act(GameAction(name='add', arguments={'amount': 2}, revision=1))
    assert game.state == {'count': 9}
    definition = counter_definition()
    definition.transitions['add'].result = '{"count": 999}'
    game = make_game(tmp_path, definition)
    with pytest.raises(ValueError):
        game.act(GameAction(name='add', arguments={'amount': 1}, revision=0))
    assert game.state == {'count': 0}
    definition.transitions['add'].result = '{"count": 1}'
    definition.invariants = ['state.count == 0']
    game = make_game(tmp_path, definition)
    with pytest.raises(ValueError, match='invariant'):
        game.act(GameAction(name='add', arguments={'amount': 1}, revision=0))
    assert game.revision == 0


def test_view_failure_and_persistence_failure_roll_back(tmp_path: Path) -> None:
    definition = counter_definition()
    definition.view.children[0].props['text'] = 'state.count == 0 ? "Ready" : 1 / 0'
    game = make_game(tmp_path, definition)
    before = game.payload()
    with pytest.raises(ValueError):
        game.act(GameAction(name='add', arguments={'amount': 1}, revision=0))
    assert game.payload() == before
    game = make_game(tmp_path)
    game._persist = Mock(side_effect=OSError('disk full'))
    before = game.payload()
    with pytest.raises(ValueError, match='disk full'):
        game.act(GameAction(name='add', arguments={'amount': 1}, revision=0))
    assert game.payload() == before


@pytest.mark.parametrize('expression', ['unknownFunction(1)', '"x".matches("x")', 'google.api.Request{}'])
def test_unapproved_cel_operations_fail_closed(expression: str) -> None:
    definition = counter_definition()
    definition.transitions['add'].result = expression
    with pytest.raises(ValueError):
        GameProgram(definition)


def test_recursive_functions_are_rejected() -> None:
    data = counter_definition().model_dump()
    data['functions'] = {'recurse': {'parameters': ['x'], 'expr': 'recurse(x)'}}
    with pytest.raises(ValueError, match='Unsupported CEL function'):
        GameProgram(GameDefinition.model_validate(data))


def test_nested_macros_share_a_work_budget() -> None:
    data = counter_definition().model_dump()
    data['constants'] = {'values': list(range(30))}
    data['derived'] = [{'name': 'pairs', 'expr': 'constants.values.map(x, constants.values.map(y, x + y))'}]
    execution = GameProgram(GameDefinition.model_validate(data)).execution()
    execution.budget.remaining = 500
    with pytest.raises(ValueError, match='budget'):
        execution.context({'count': 0})


@pytest.mark.parametrize('schema', [{'$ref': 'https://example.com/schema'}, {'type': 'string', 'pattern': '(a+)+$'}])
def test_schema_network_access_and_regex_are_rejected(schema: dict[str, str]) -> None:
    definition = counter_definition()
    definition.state_schema = schema
    with pytest.raises(ValueError):
        GameProgram(definition)


def test_missing_invalid_and_changed_packages(tmp_path: Path) -> None:
    assert GameState(tmp_path, Mock(), Mock()).payload() is None
    game = make_game(tmp_path)
    saved = game.serialize()['game_state']
    saved['definition_hash'] = 'old'
    game.load(saved)
    assert 'changed' in game.error
    assert game.revision == 0
    (tmp_path / 'game/game.json').write_text('{}', encoding='utf-8')
    broken = GameState(tmp_path, Mock(), Mock())
    assert broken.error
    with pytest.raises(ValueError):
        broken.act(GameAction(name='anything', revision=0))


def test_schema_property_names_are_not_treated_as_schema_keywords() -> None:
    from components.canvas.game_runtime import validate_schema

    validator = validate_schema({"type": "object", "properties": {"pattern": {"type": "string"}, "$ref": {"type": "integer"}}})
    validator.validate({"pattern": "any game data", "$ref": 3})
