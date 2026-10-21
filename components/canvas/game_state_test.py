"""Game-agnostic contract tests: the same runtime also runs a counter."""
from concurrent.futures import ThreadPoolExecutor
import json
import logging
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from components.canvas.game_runtime import (
    MAX_GAME_BYTES,
    GameDefinition,
    GameProgram,
    GameStep,
    ViewAction,
    ViewNode,
    validate_game_package,
    validate_game_svg,
)
from components.canvas.game_state import (
    GameAction,
    GameState,
    StaleGameAction,
    UnauthorizedGameAction,
)
from components.canvas.canvas_state_manager import CanvasStateManager


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


def test_local_draft_replays_as_one_durable_player_event(tmp_path: Path) -> None:
    definition = counter_definition()
    definition.local_preview = True
    definition.transitions['add'].triggering = False
    definition.transitions['clear'].result = '{"count": state.count}'
    game = make_game(tmp_path, definition)
    persist, notify, callback = Mock(), Mock(), Mock()
    game._persist, game._notify, game.on_player_action = persist, notify, callback
    action = GameAction(name='clear', revision=0, draft=[
        GameStep(name='add', arguments={'amount': 2}),
        GameStep(name='add', arguments={'amount': 3}),
    ])
    result = game.act(action)
    assert result['revision'] == 1
    assert game.state == {'count': 5}
    persist.assert_called_once()
    notify.assert_called_once()
    callback.assert_called_once()
    assert callback.call_args.args[0] == action
    assert callback.call_args.args[1]['state'] == {'count': 5}
    restored = make_game(tmp_path, definition)
    restored.load(game.serialize()['game_state'])
    assert restored.pending_story_actions()[0][0] == action
    with pytest.raises(StaleGameAction):
        game.act(action)
    callback.assert_called_once()


@pytest.mark.parametrize('invalid', [
    GameStep(name='add', arguments={'amount': 10}),
    GameStep(name='add', arguments={'amount': '2'}),
    GameStep(name='missing'),
    GameStep(name='clear'),
])
def test_invalid_draft_rolls_back_every_step(tmp_path: Path, invalid: GameStep) -> None:
    definition = counter_definition()
    definition.local_preview = True
    definition.transitions['add'].triggering = False
    game = make_game(tmp_path, definition)
    before = game.serialize()
    callback = Mock()
    game.on_player_action = callback
    with pytest.raises(ValueError):
        game.act(GameAction(name='clear', revision=0, draft=[
            GameStep(name='add', arguments={'amount': 1}), invalid,
        ]))
    assert game.serialize() == before
    assert game.payload()['view'][0]['children'][0]['props']['text'] == 'Count: 0'
    callback.assert_not_called()


def test_draft_cannot_smuggle_orator_action_as_contributor(tmp_path: Path) -> None:
    definition = counter_definition()
    definition.local_preview = True
    definition.transitions['add'].triggering = False
    definition.transitions['clear'].permission = 'contributor'
    game = make_game(tmp_path, definition)
    before = game.serialize()
    with pytest.raises(UnauthorizedGameAction):
        game.act(GameAction(name='clear', revision=0, draft=[
            GameStep(name='add', arguments={'amount': 1}),
        ]), actor='contributor')
    assert game.serialize() == before


def test_draft_cannot_smuggle_narratron_action(tmp_path: Path) -> None:
    definition = counter_definition()
    definition.local_preview = True
    definition.transitions['add'].actor = 'narratron'
    definition.view.children = [definition.view.children[0]]
    game = make_game(tmp_path, definition)
    with pytest.raises(ValueError, match='exploratory'):
        game.act(GameAction(name='clear', revision=0, draft=[
            GameStep(name='add', arguments={'amount': 1}),
        ]))
    assert game.state == {'count': 0}


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
    assert game.payload()['view'] == before['view']
    assert game.revision == before['revision']
    assert game.payload()['error']
    game._notify.assert_called_once_with('latest')
    game = make_game(tmp_path)
    game._persist = Mock(side_effect=OSError('disk full'))
    before = game.payload()
    with pytest.raises(ValueError, match='disk full'):
        game.act(GameAction(name='add', arguments={'amount': 1}, revision=0))
    assert game.payload()['view'] == before['view']
    assert game.revision == before['revision']
    assert game.payload()['error']
    game._persist = Mock()
    game.act(GameAction(name='add', arguments={'amount': 1}, revision=0))
    assert game.payload()['error'] == ''


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


def test_view_cannot_expose_narratron_only_action() -> None:
    definition = counter_definition()
    definition.transitions["add"].actor = "narratron"
    with pytest.raises(ValueError, match="Narratron-only"):
        validate_game_package(definition.model_dump_json().encode("utf-8"))


def test_model_projection_and_available_narratron_actions(tmp_path: Path) -> None:
    definition = counter_definition()
    definition.model_state = '{"summary": "Count " + string(state.count)}'
    definition.transitions['clear'].actor = 'narratron'
    definition.transitions['clear'].available = 'state.count > 0'
    game = make_game(tmp_path, definition)
    assert game.story_snapshot('narratron')['state'] == {'summary': 'Count 0'}
    assert game.story_snapshot('narratron')['actions'] == {}
    game.act(GameAction(name='add', arguments={'amount': 2}, revision=0))
    snapshot = game.story_snapshot('narratron')
    assert snapshot['state'] == {'summary': 'Count 2'}
    assert set(snapshot['actions']) == {'clear'}
    assert game.state == {'count': 2}


@pytest.mark.parametrize('field', ['model_state', 'available'])
def test_model_context_failure_rolls_back_action(tmp_path: Path, field: str) -> None:
    definition = counter_definition()
    definition.transitions['clear'].actor = 'narratron'
    if field == 'model_state':
        definition.model_state = 'state.count == 0 ? state.count : 1 / 0'
    else:
        definition.transitions['clear'].available = 'state.count == 0 ? true : 1 / 0 == 0'
    game = make_game(tmp_path, definition)
    before = game.story_snapshot('narratron')
    with pytest.raises(ValueError):
        game.act(GameAction(name='add', arguments={'amount': 1}, revision=0))
    assert game.story_snapshot('narratron') == before
    assert game.revision == 0


def test_action_availability_cannot_reference_arguments() -> None:
    definition = counter_definition()
    definition.transitions['clear'].available = 'action.item == "x"'
    with pytest.raises(ValueError, match='availability'):
        GameProgram(definition)


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


@pytest.mark.parametrize('failure', ['schema', 'render', 'unexpected'])
def test_broken_save_does_not_interrupt_canvas_loading(tmp_path: Path, failure: str) -> None:
    definition = counter_definition()
    if failure == 'render':
        definition.view.children[0].props['text'] = 'state.count == 0 ? "Ready" : 1 / 0'
    game = make_game(tmp_path, definition)
    saved = game.serialize()['game_state']
    saved['state'] = {'count': -1 if failure == 'schema' else 1}
    (tmp_path / 'theater.json').write_text(json.dumps({'canvas_state': {
        'game_state': saved, 'chat_messages': ['still here'], 'suggestions': [],
    }}), encoding='utf-8')
    manager = Mock(game=game)
    manager.theater.directory.return_value = tmp_path
    before = game.serialize()
    if failure == 'unexpected':
        with patch.object(game, '_prepare', side_effect=RuntimeError('broken renderer')):
            CanvasStateManager.load_state_from_disk(manager)
    else:
        CanvasStateManager.load_state_from_disk(manager)
    assert game.serialize() == before
    assert 'could not be loaded' in game.payload()['error']
    assert manager.chat.messages == ['still here']
    manager.chat.load_suggestions.assert_called_once_with([])


def test_notification_failure_keeps_committed_action(tmp_path: Path) -> None:
    game = make_game(tmp_path)
    game._notify = Mock(side_effect=RuntimeError('connection closed'))
    result = game.act(GameAction(name='add', arguments={'amount': 1}, revision=0))
    assert result['revision'] == 1
    assert game.state == {'count': 1}
    restored = make_game(tmp_path)
    restored.load(game.serialize()['game_state'])
    assert restored.state == game.state
    with pytest.raises(StaleGameAction):
        game.act(GameAction(name='add', arguments={'amount': 1}, revision=0))


def test_player_actions_wake_story_callback_after_commit(tmp_path: Path) -> None:
    game = make_game(tmp_path)
    callback = Mock(side_effect=RuntimeError("planner unavailable"))
    game.on_player_action = callback
    action = GameAction(name='add', arguments={'amount': 1}, revision=0)
    game.act(action)
    callback.assert_called_once_with(action, game.pending_story_actions()[0][1])
    assert game.state == {'count': 1}
    assert game.revision == 1


def test_player_action_event_preserves_each_committed_state(tmp_path: Path) -> None:
    game = make_game(tmp_path)
    callback = Mock()
    game.on_player_action = callback
    game.act(GameAction(name='add', arguments={'amount': 1}, revision=0))
    game.act(GameAction(name='add', arguments={'amount': 2}, revision=1))
    assert callback.call_args_list[0].args[1]['state'] == {'count': 1}
    assert callback.call_args_list[0].args[1]['revision'] == 1
    assert callback.call_args_list[1].args[1]['state'] == {'count': 3}
    assert callback.call_args_list[1].args[1]['revision'] == 2


def test_game_state_emits_debug_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="components.canvas.game_state"):
        game = make_game(tmp_path)
        game.story_snapshot("responder")
        game.serialize()
        game.act(GameAction(name="add", arguments={"amount": 1}, revision=0))
        game.report_error("debug check")
        saved = game.serialize()["game_state"]
        game.load(saved)

    log_text = caplog.text
    assert "[GameState] Loaded game package from" in log_text
    assert "[GameState] Preparing state (revision=0)" in log_text
    assert "[GameState] Prepared state successfully" in log_text
    assert "[GameState] Generating story snapshot for audience 'responder'" in log_text
    assert "[GameState] Serializing game state (revision=0)" in log_text
    assert "[GameState] Executing action 'add' by actor 'player'" in log_text
    assert "[GameState] Action 'add' applied: revision advanced to 1" in log_text
    assert "[GameState] Reporting error (revision=1): debug check" in log_text
    assert "[GameState] Notifying canvas listener at revision=1" in log_text
    assert "[GameState] Loading saved game (current_revision=1)" in log_text
    assert "[GameState] Restored saved game to revision=1" in log_text


def test_non_triggering_player_action_does_not_wake_story_callback(tmp_path: Path) -> None:
    definition = counter_definition()
    definition.transitions["add"].triggering = False
    game = make_game(tmp_path, definition)
    callback = Mock()
    game.on_player_action = callback
    game.act(GameAction(name="add", arguments={"amount": 1}, revision=0))
    callback.assert_not_called()
    assert game.state == {"count": 1}
    assert game.revision == 1


def test_example_adventure_checkers_narratron_interaction(tmp_path: Path) -> None:
    example_path = Path("adventures/example_adventure")
    game = GameState(example_path, Mock(), Mock())
    callback = Mock()
    game.on_player_action = callback

    # Initial state is Red's turn (turn == 1). Narratron actions should be empty.
    snap = game.story_snapshot("narratron")
    assert snap is not None
    assert snap["instructions"] != ""
    state_dict = snap["state"]
    assert type(state_dict) is dict
    assert state_dict["turn"] == 1
    assert state_dict["turn_name"] == "Red"
    assert state_dict["red_count"] == 12
    assert state_dict["ivory_count"] == 12
    assert snap["actions"] == {}

    # Player selects a piece (triggering: false). Callback must NOT be invoked.
    game.act(GameAction(name="select", arguments={"index": 40}, revision=0), actor="player")
    callback.assert_not_called()
    assert game.state["selected"] == 40
    assert game.revision == 1

    # Player moves piece (triggering: true). Callback MUST be invoked.
    game.act(GameAction(name="move", arguments={"source": 40, "target": 33}, revision=1), actor="player")
    callback.assert_called_once()
    assert game.state["turn"] == -1
    assert game.revision == 2

    # Now it is Ivory's turn (turn == -1). Narratron snapshot should include narratron_move.
    snap_after_move = game.story_snapshot("narratron")
    assert snap_after_move is not None
    actions_dict = snap_after_move["actions"]
    assert type(actions_dict) is dict
    assert "narratron_move" in actions_dict

    # Narratron executes an Ivory move.
    game.act(GameAction(name="narratron_move", arguments={"source": 17, "target": 26}, revision=2), actor="narratron")
    assert game.state["turn"] == 1
    assert game.revision == 3


def test_game_package_size_limit_is_128kb(tmp_path: Path) -> None:
    assert MAX_GAME_BYTES == 131_072
    definition = counter_definition()
    # Build a ~95 KiB package (above the old 64 KiB limit, within 128 KiB).
    definition.constants = {"lore_table": ["x" * 900 for _ in range(95)]}
    raw = definition.model_dump_json().encode("utf-8")
    assert 65_536 < len(raw) <= MAX_GAME_BYTES
    game = make_game(tmp_path, definition)
    assert game.error == ""
    assert game.state == {"count": 0}

    oversized = b" " * (MAX_GAME_BYTES + 1)
    with pytest.raises(ValueError, match="size limit"):
        validate_game_package(oversized)


def test_svg_graphics_from_game_folder_render_and_validate(tmp_path: Path) -> None:
    game_dir = tmp_path / "game"
    game_dir.mkdir(parents=True, exist_ok=True)
    svg_text = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<g fill="#eab308" stroke="#1e293b" stroke-width="2">'
        '<path d="M12 52 L52 52 L44 20 L20 20 Z"/>'
        '<circle cx="32" cy="14" r="6"/>'
        '</g>'
        '</svg>'
    )
    (game_dir / "crown.svg").write_text(svg_text, encoding="utf-8")

    definition = counter_definition()
    definition.view.children.append(
        ViewNode(
            kind="svg",
            props={"src": '"crown.svg"', "width": '"48"', "id": '"crown-graphic"'},
        )
    )
    (game_dir / "game.json").write_text(definition.model_dump_json(), encoding="utf-8")

    game = GameState(tmp_path, Mock(), Mock())
    assert game.error == ""
    payload = game.payload()
    assert payload is not None
    view_list = payload["view"]
    assert type(view_list) is list
    root_node = view_list[0]
    assert type(root_node) is dict
    children = root_node["children"]
    assert type(children) is list
    svg_node = children[-1]
    assert type(svg_node) is dict
    assert svg_node["kind"] == "svg"
    assert svg_node["props"]["viewBox"] == "0 0 64 64"
    assert svg_node["props"]["width"] == "48"
    assert svg_node["props"]["src"] == "crown.svg"
    svg_children = svg_node["children"]
    assert type(svg_children) is list
    assert len(svg_children) == 1
    g_node = svg_children[0]
    assert type(g_node) is dict
    assert g_node["kind"] == "g"
    assert g_node["props"]["fill"] == "#eab308"
    assert len(g_node["children"]) == 2
    assert g_node["children"][0]["kind"] == "path"
    assert g_node["children"][1]["kind"] == "circle"


@pytest.mark.parametrize(
    "bad_svg",
    [
        '<svg><script>alert(1)</script></svg>',
        '<svg onload="alert(1)"><circle cx="5" cy="5" r="4"/></svg>',
        '<!DOCTYPE svg [<!ENTITY xxe "bad">]><svg/>',
        '<svg><foreignObject><div>html</div></foreignObject></svg>',
        '<svg><rect fill="url(https://evil.test/x)"/></svg>',
    ],
)
def test_unsafe_svg_graphics_are_rejected(bad_svg: str) -> None:
    with pytest.raises(ValueError):
        validate_game_svg(bad_svg.encode("utf-8"))


def test_contributor_permission_actions_and_narration(tmp_path: Path) -> None:
    definition = counter_definition()
    definition.transitions["add"].permission = "contributor"
    # 'clear' remains default permission ('orator')
    definition.view.children.append(
        ViewNode(
            kind="button",
            props={"text": '"Clear"'},
            action=ViewAction(name='"clear"', arguments="{}"),
        )
    )
    game = make_game(tmp_path, definition)
    callback = Mock()
    game.on_player_action = callback

    payload = game.payload()
    assert payload is not None
    children = payload["view"][0]["children"]
    add_btn = children[1]
    clear_btn = children[3]
    assert add_btn["action"]["permission"] == "contributor"
    assert clear_btn["action"]["permission"] == "orator"

    # Contributor can take 'add' action and trigger narration callback
    action = GameAction(name="add", arguments={"amount": 2}, revision=0)
    game.act(action, actor="contributor")
    assert game.state == {"count": 2}
    assert game.revision == 1
    callback.assert_called_once_with(action, game.pending_story_actions()[0][1])

    # Contributor cannot take orator-only 'clear' action
    with pytest.raises(UnauthorizedGameAction, match="Only the active orator"):
        game.act(GameAction(name="clear", revision=1), actor="contributor")
    assert game.state == {"count": 2}
    assert game.revision == 1
