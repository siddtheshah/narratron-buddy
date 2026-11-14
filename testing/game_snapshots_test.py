"""Validate the browser game snapshots against live Python game output.

Definitions below retain the original backend setup from the migrated UI tests.
"""
from pathlib import Path
from unittest.mock import Mock
import json

import pytest
from pydantic import BaseModel, JsonValue, TypeAdapter

from game_engine.game_runtime import GameAction, GameDefinition, GameStep, ViewNode
from game_engine.game_state import GameState
from testing.game_fixtures import counter_definition, panel_definition, make_game, make_preview_checkers_game, make_example_canvas


def make_form_game(tmp_path: Path) -> GameState:
    definition = GameDefinition.model_validate({
        "version": 2,
        "initial": {"text": "", "number": 0},
        "state_schema": {
            "type": "object", "required": ["text", "number"], "additionalProperties": False,
            "properties": {"text": {"type": "string", "maxLength": 1000}, "number": {"type": "integer"}},
        },
        "transitions": {"save": {
            "parameters": {
                "type": "object", "required": ["text", "number"], "additionalProperties": False,
                "properties": {"text": {"type": "string", "maxLength": 1000}, "number": {"type": "integer"}},
            },
            "result": '{"text": action.text, "number": action.number}',
        }},
        "view": {"kind": "column", "children": [
            {"kind": "text", "props": {"text": "state.text"}},
            {"kind": "form", "props": {"label": '"Example form"'},
             "action": {"name": '"save"', "arguments": '{"number": 3}'}, "children": [
                 {"kind": "input", "props": {
                     "name": '"text"', "label": '"Text"', "labelHidden": "true", "required": "true", "maxLength": "9000",
                 }},
                 {"kind": "input", "props": {
                     "name": '"number"', "label": '"Number"', "type": '"number"',
                     "value": "0", "required": "true", "step": "1",
                 }},
                 {"kind": "button", "props": {"text": '"Save"'}},
                 {"kind": "button", "props": {"text": '"Clear text"', "clearInputs": '{"text": ""}'}},
             ]},
        ]},
    })
    game = make_game(tmp_path, definition)
    return game


def make_panel_game(tmp_path: Path) -> GameState:
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "pieces").mkdir()
    (game_dir / "pieces/token.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32"><circle cx="16" cy="16" r="12" fill="red" /></svg>',
        encoding="utf-8",
    )
    definition = panel_definition()
    definition.constants["unsafe"] = '</script><script>parent.compromised = true</script><img src="https://evil.test/injected">'
    assert definition.panel is not None
    definition.panel.parameters["unsafe"] = "constants.unsafe"
    # Revalidate to regenerate the internal view from the panel contract.
    (game_dir / "game.json").write_text(definition.model_dump_json(exclude={"view"}), encoding="utf-8")
    (game_dir / "panel.html").write_text('''<!doctype html><html><head><style>
        @import "https://evil.test/import.css";
        p { background-image: url(https://evil.test/background); font-family: remote; }
        @font-face {font-family: remote; src: url(https://evil.test/font);}
        </style></head><body>
        <p data-panel-text="count"></p><pre data-panel-text="unsafe"></pre>
        <img src="pieces/token.svg" alt="Token" width="32" height="32">
        <button data-panel-action="increment" data-panel-disabled="full">Add one</button>
        <button data-panel-action="unknown">Unknown action</button>
        </body></html>''', encoding="utf-8")
    game = GameState(tmp_path, Mock(), Mock(), backend="python")
    assert not game.error
    return game


def make_runtime_counter(tmp_path: Path) -> GameState:
    definition = counter_definition()
    definition.local_preview = True
    definition.view.props.update({
        'negativeDivision': '-7 / 2',
        'remainder': '-7 % 2',
        'boolean': '!false && (state.count >= 0 || false)',
        'membership': '"key" in {"key": 1}',
        'deepEquality': '[{"a": 1, "b": 2}] == [{"b": 2, "a": 1}]',
        'filteredMap': '[1, 2, 3].filter(x, x > 1).map(x, x * 2)',
        'filtered': '[1, 2, 3].filter(x, x != 2)',
        'all': '[1, 2].all(x, x < 3)',
        'exists': '[1, 2].exists(x, x == 2)',
        'unicodeSize': 'size("🌍")',
        'converted': 'int("3") + int(true)',
        'nullValue': 'null',
    })
    return make_game(tmp_path, definition)


SNAPSHOTS = TypeAdapter(dict[str, dict[str, JsonValue]]).validate_json(
    Path("testing/testdata/game_snapshots.json").read_text(encoding="utf-8")
)


def assert_snapshot(name: str, payload: dict[str, JsonValue] | None) -> None:
    assert payload is not None
    expected = dict(SNAPSHOTS[name])
    # HTML authored on Windows was captured with CRLF in the migration fixture.
    for data in (payload, expected):
        if "panel_html" in data:
            data["panel_html"] = str(data["panel_html"]).replace("\r\n", "\n")
    assert payload == expected, name


class RuntimeStep(BaseModel):
    step: GameStep
    state: dict[str, JsonValue]
    view: list[JsonValue]


class RuntimeCase(BaseModel):
    payload: dict[str, JsonValue]
    steps: list[RuntimeStep]
    draft_move_result: dict[str, JsonValue] | None = None


@pytest.mark.parametrize("package", ["counter", "checkers"])
def test_runtime_snapshots_match_live_engine(tmp_path: Path, package: str) -> None:
    case = RuntimeCase.model_validate(SNAPSHOTS[f"runtime_{package}"])
    game = make_runtime_counter(tmp_path) if package == "counter" else make_preview_checkers_game(tmp_path)
    assert game.payload() == case.payload
    for step in case.steps:
        payload = game.act(GameAction(
            name=step.step.name, arguments=step.step.arguments, revision=game.revision,
        ))
        assert game.state == step.state
        assert payload["view"] == step.view
    if case.draft_move_result is not None:
        game = make_preview_checkers_game(tmp_path)
        payload = game.act(GameAction(
            name="move", arguments={"source": 42, "target": 35}, revision=0,
            draft=[GameStep(name="select", arguments={"index": 40}), GameStep(name="select", arguments={"index": 42})],
        ))
        assert payload == case.draft_move_result


@pytest.mark.parametrize("failure", ["guard", "invariant", "evaluation"])
def test_failure_snapshots_match_live_engine(tmp_path: Path, failure: str) -> None:
    definition = counter_definition()
    definition.local_preview = True
    definition.transitions["add"].preview = True
    if failure == "guard":
        definition.transitions["add"].guard = "false"
    elif failure == "invariant":
        definition.invariants = ["state.count == 0"]
    else:
        definition.transitions["add"].result = '{"count": 1 / 0}'
    assert_snapshot(f"fail_{failure}", make_game(tmp_path, definition).payload())


def test_form_snapshots_match_live_engine(tmp_path: Path) -> None:
    game = make_form_game(tmp_path)
    assert_snapshot("form_0", game.payload())
    assert_snapshot("form_1", game.act(GameAction(
        name="save", arguments={"text": '<img src=x onerror="window.compromised=true">', "number": 3}, revision=0,
    )))


def test_panel_snapshots_match_live_engine(tmp_path: Path) -> None:
    game = make_panel_game(tmp_path)
    assert_snapshot("panel_0", game.payload())
    assert_snapshot("panel_1", game.act(GameAction(name="add", arguments={"amount": 1}, revision=0)))


def test_badge_snapshots_match_live_engine(tmp_path: Path) -> None:
    (tmp_path / "game").mkdir()
    (tmp_path / "game/badge.svg").write_text(
        '<svg viewBox="0 0 24 24"><g fill="#facc15"><path d="M12 2 L15 9 L22 9 L16 14 L18 21 L12 17 L6 21 L8 14 L2 9 L9 9 Z"/></g></svg>',
        encoding="utf-8",
    )
    definition = counter_definition()
    definition.view.children.append(ViewNode(kind="svg", props={"src": '"badge.svg"', "id": '"svg-badge"'}))
    game = make_game(tmp_path, definition)
    assert_snapshot("counter_badge_0", game.payload())
    assert_snapshot("counter_badge_1", game.act(GameAction(name="add", arguments={"amount": 1}, revision=0)))
    game.report_error("Game action failed; the previous game state was retained.")
    assert_snapshot("counter_badge_err", game.payload())
    assert_snapshot("counter_badge_2", game.act(GameAction(name="add", arguments={"amount": 1}, revision=1)))


@pytest.mark.parametrize("near_win", [False, True])
def test_canvas_checkers_snapshots_match_live_engine(tmp_path: Path, near_win: bool) -> None:
    canvas = make_example_canvas(tmp_path)
    game = canvas.game
    assert game is not None and game.program is not None
    if near_win:
        game.load({"definition_hash": game.definition_hash, "revision": 0, "state": {
            **game.program.definition.initial,
            "board": [1 if i == 42 else -1 if i == 35 else 0 for i in range(64)],
        }})
    prefix = "checkers_near_win" if near_win else "example_checkers"
    assert_snapshot(f"{prefix}_0", game.payload())
    steps = [
        GameStep(name="select", arguments={"index": 42}),
        GameStep(name="move", arguments={"source": 42, "target": 28}),
    ] if near_win else [
        GameStep(name="select", arguments={"index": 40}),
        GameStep(name="move", arguments={"source": 40, "target": 33}),
        GameStep(name="select", arguments={"index": 17}),
        GameStep(name="move", arguments={"source": 17, "target": 26}),
    ]
    for index, step in enumerate(steps, 1):
        payload = game.act(GameAction(
            name=step.name, arguments=step.arguments, revision=game.revision,
            canvas_revision=canvas.canvas_observation().revision,
        ))
        assert_snapshot(f"{prefix}_{index}", payload)
    if near_win:
        snapshot = canvas.doodles.snapshot()
        expected = json.loads(json.dumps(SNAPSHOTS["checkers_near_win_stamps"]))
        # Only the page and annotation IDs are allocated randomly at runtime.
        expected["page_id"] = snapshot["page_id"]
        expected["stamps"][0]["id"] = snapshot["stamps"][0]["id"]
        # Local storage paths are omitted from the browser wire fixture.
        assert Path(snapshot["stamps"][0].pop("asset_path")).is_file()
        assert snapshot == expected
