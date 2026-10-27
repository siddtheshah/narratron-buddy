from pathlib import Path
from unittest.mock import Mock

import pytest

from game.game_client import browser_program
from game.game_runtime import FunctionDefinition, GameAction, GameProgram
from game.game_state import GameState
from testing.game_fixtures import counter_definition


def test_browser_rules_are_opt_in_and_exclude_model_instructions() -> None:
    definition = counter_definition()
    definition.instructions = 'Private merchant decisions'
    definition.transitions['clear'].actor = 'narratron'
    assert browser_program(GameProgram(definition)) is None
    definition.local_preview = True
    client = browser_program(GameProgram(definition))
    assert client is not None
    assert set(client['transitions']) == {'add'}
    assert 'instructions' not in client
    assert 'model_state' not in client


def test_unsupported_browser_expression_preserves_server_rendering(tmp_path: Path) -> None:
    definition = counter_definition()
    definition.local_preview = True
    definition.view.props['opacity'] = '0.5'
    directory = tmp_path / 'game'
    directory.mkdir()
    (directory / 'game.json').write_text(definition.model_dump_json(), encoding='utf-8')
    game = GameState(tmp_path, Mock(), Mock())
    assert game.error == ''
    assert 'client' not in game.payload()
    assert game.payload()['view'][0]['props']['opacity'] == 0.5


@pytest.mark.parametrize('helper', [False, True])
def test_random_rules_use_server_rendering(tmp_path: Path, helper: bool) -> None:
    definition = counter_definition()
    definition.local_preview = True
    source = 'random_int(1, 6)'
    if helper:
        definition.functions['roll'] = FunctionDefinition(parameters=[], expr=source)
        source = 'roll()'
    definition.transitions['clear'].result = '{"count": ' + source + '}'
    directory = tmp_path / 'game'
    directory.mkdir()
    (directory / 'game.json').write_text(definition.model_dump_json(), encoding='utf-8')
    game = GameState(tmp_path, Mock(), Mock())
    assert game.error == ''
    assert 'client' not in game.payload()
    game.act(GameAction(name='clear', revision=0))
    assert 1 <= game.state['count'] <= 6
