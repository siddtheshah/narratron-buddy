"""Small non-board game used to exercise the generic game contracts."""
from pathlib import Path
from typing import Literal
from unittest.mock import Mock

from game.game_runtime import GameDefinition
from game.game_state import GameState


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


def exchange_definition() -> GameDefinition:
    """Small temporary package for actor, inventory and two-party commit tests."""
    initial = {
        'player_inventory': ['red', 'blue'],
        'narratron_inventory': ['green'],
        'player_offer': [],
        'narratron_offer': [],
        'player_done': False,
        'narratron_done': False,
        'trades': 0,
    }
    return GameDefinition.model_validate({
        'version': 2,
        'instructions': 'Test exchange: each side acts only for itself.',
        'model_state': '{"owned": state.player_inventory, "stock": state.narratron_inventory, "table": {"player": state.player_offer, "narratron": state.narratron_offer}, "ready": {"player": state.player_done, "narratron": state.narratron_done}, "trades": state.trades}',
        'initial': initial,
        'state_schema': {
            'type': 'object',
            'properties': {
                **{name: {'type': 'array', 'items': {'type': 'string'}, 'uniqueItems': True}
                   for name in ('player_inventory', 'narratron_inventory', 'player_offer', 'narratron_offer')},
                'player_done': {'type': 'boolean'},
                'narratron_done': {'type': 'boolean'},
                'trades': {'type': 'integer', 'minimum': 0},
            },
            'required': list(initial),
            'additionalProperties': False,
        },
        'functions': {
            'settle': {
                'parameters': ['s', 'player_done', 'narratron_done'],
                'expr': '{"player_inventory": player_done && narratron_done ? s.player_inventory.filter(x, !(x in s.player_offer)) + s.narratron_offer : s.player_inventory, "narratron_inventory": player_done && narratron_done ? s.narratron_inventory.filter(x, !(x in s.narratron_offer)) + s.player_offer : s.narratron_inventory, "player_offer": player_done && narratron_done ? [] : s.player_offer, "narratron_offer": player_done && narratron_done ? [] : s.narratron_offer, "player_done": player_done && !narratron_done, "narratron_done": narratron_done && !player_done, "trades": s.trades + (player_done && narratron_done ? 1 : 0)}',
            },
        },
        'transitions': {
            'player_place': {
                'actor': 'player',
                'parameters': {'type': 'object', 'properties': {'item': {'type': 'string'}}, 'required': ['item'], 'additionalProperties': False},
                'guard': '!state.player_done && !state.narratron_done && action.item in state.player_inventory && !(action.item in state.player_offer)',
                'result': '{"player_inventory": state.player_inventory, "narratron_inventory": state.narratron_inventory, "player_offer": state.player_offer + [action.item], "narratron_offer": state.narratron_offer, "player_done": false, "narratron_done": false, "trades": state.trades}',
            },
            'narratron_place': {
                'actor': 'narratron',
                'description': 'Place one available stock item.',
                'available': '!state.player_done && !state.narratron_done && state.narratron_inventory.filter(x, !(x in state.narratron_offer)).size() > 0',
                'parameters': {'type': 'object', 'properties': {'item': {'type': 'string'}}, 'required': ['item'], 'additionalProperties': False},
                'guard': '!state.player_done && !state.narratron_done && action.item in state.narratron_inventory && !(action.item in state.narratron_offer)',
                'result': '{"player_inventory": state.player_inventory, "narratron_inventory": state.narratron_inventory, "player_offer": state.player_offer, "narratron_offer": state.narratron_offer + [action.item], "player_done": false, "narratron_done": false, "trades": state.trades}',
            },
            'player_finalize': {
                'actor': 'player',
                'parameters': {'type': 'object', 'additionalProperties': False},
                'guard': '!state.player_done && state.player_offer.size() + state.narratron_offer.size() > 0',
                'result': 'settle(state, true, state.narratron_done)',
            },
            'narratron_finalize': {
                'actor': 'narratron',
                'description': 'Accept the current exchange.',
                'available': '!state.narratron_done && state.player_offer.size() + state.narratron_offer.size() > 0',
                'parameters': {'type': 'object', 'additionalProperties': False},
                'guard': '!state.narratron_done && state.player_offer.size() + state.narratron_offer.size() > 0',
                'result': 'settle(state, state.player_done, true)',
            },
        },
        'view': {'kind': 'column', 'children': [
            {'kind': 'text', 'props': {'text': '"Trades: " + string(state.trades)'}},
        ]},
    })


def make_game(
    directory: Path,
    definition: GameDefinition | None = None,
    *,
    backend: Literal["auto", "go", "python"] = "auto",
) -> GameState:
    (directory / 'game').mkdir(parents=True, exist_ok=True)
    (directory / 'game/game.json').write_text((definition or counter_definition()).model_dump_json(), encoding='utf-8')
    return GameState(directory, Mock(), Mock(), backend=backend)
