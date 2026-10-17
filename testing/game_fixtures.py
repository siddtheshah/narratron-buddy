"""Small non-board game used to exercise the generic game contracts."""
from pathlib import Path
from unittest.mock import Mock

from components.canvas.game_runtime import GameDefinition
from components.canvas.game_state import GameState


def counter_definition() -> GameDefinition:
    return GameDefinition.model_validate({
        'version': 2, 'initial': {'count': 0},
        'state_schema': {'type': 'object', 'properties': {'count': {'type': 'integer', 'minimum': 0, 'maximum': 10}}, 'required': ['count'], 'additionalProperties': False},
        'derived': [{'name': 'doubled', 'expr': 'state.count * 2'}],
        'invariants': ['state.count <= 10'],
        'transitions': {
            'add': {'parameters': {'type': 'object', 'properties': {'amount': {'type': 'integer', 'minimum': 1, 'maximum': 10}}, 'required': ['amount'], 'additionalProperties': False}, 'guard': 'state.count + action.amount <= 10', 'result': '{"count": state.count + action.amount}'},
            'clear': {'parameters': {'type': 'object', 'additionalProperties': False}, 'result': 'initial'},
        },
        'view': {'kind': 'column', 'children': [
            {'kind': 'text', 'props': {'text': '"Count: " + string(state.count)', 'id': '"counter"'}},
            {'kind': 'button', 'props': {'text': '"Add one"', 'disabled': 'state.count >= 10'}, 'action': {'name': '"add"', 'arguments': '{"amount": 1}'}},
            {'kind': 'svg', 'props': {'viewBox': '"0 0 10 10"'}, 'children': [{'kind': 'circle', 'props': {'cx': '5', 'cy': '5', 'r': 'state.count / 2', 'fill': '"#67e8f9"'}}]},
        ]},
    })


def make_game(directory: Path, definition: GameDefinition | None = None) -> GameState:
    (directory / 'game').mkdir(parents=True, exist_ok=True)
    (directory / 'game/game.json').write_text((definition or counter_definition()).model_dump_json(), encoding='utf-8')
    return GameState(directory, Mock(), Mock())


