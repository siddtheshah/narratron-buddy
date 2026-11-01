"""Both CEL engines prepare the same canvas-dependent candidates and commands."""
from collections.abc import Iterator
from copy import deepcopy
import os
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

from game_engine.canvas_contract import CanvasAPI, CanvasSnapshot
from game_engine.canvas_contract_test import scene_snapshot
from game_engine.canvas_evaluation import CanvasEvaluator
from game_engine.game_client import browser_program
from game_engine.game_runtime import (
    Binding, FunctionDefinition, GameDefinition, GameProgram, GameStep, KeyboardDefinition,
    ViewNode, validate_game_package,
)
from game_engine.game_state import GameAction, GameState
from game_engine.go_cel import GoWorker
from testing.game_fixtures import counter_definition


@pytest.fixture(params=["python", "go"])
def worker(request: pytest.FixtureRequest) -> Iterator[GoWorker | None]:
    if request.param == "python":
        yield None
        return
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


def canvas_definition() -> GameDefinition:
    definition = counter_definition()
    definition.canvas_api = CanvasAPI(version=1, effects=["show_scene", "put_stamp", "set_overlay"])
    definition.transitions["add"].guard = "canvas.page != null && canvas.event != null && canvas.event.kind == 'region_activated' && 'lock' in canvas.event.region_ids && state.count + action.amount <= 10"
    definition.transitions["add"].canvas_effects = '[{"op": "put_stamp", "target": {"page_id": canvas.page.id}, "key": "piece", "asset_id": "stamp:key", "center": {"x": next_state.count * 1000, "y": 5000}, "width": 600, "rotation": 0, "draggable": true}]'
    definition.view = ViewNode(kind="text", props={"text": "canvas.event == null ? string(canvas.revision) : 'event leaked'"})
    definition.transitions["clear"].actor = "narratron"
    definition.transitions["clear"].available = "canvas.page != null && canvas.event == null"
    definition.model_state = '{"count": state.count}'
    return definition


def region_snapshot() -> CanvasSnapshot:
    data = scene_snapshot().model_dump(mode="json")
    data["event"] = {
        "id": "event-1", "kind": "region_activated", "actor": "player", "page_id": "page-7",
        "canvas_revision": 42, "object_ids": [], "point": {"x": 7300, "y": 5000},
        "region_ids": ["lock"], "key": None, "binding_id": None,
    }
    return CanvasSnapshot.model_validate(data)


def test_attempt_returns_next_state_and_commands_without_changing_inputs(worker: GoWorker | None) -> None:
    definition = canvas_definition()
    evaluator = CanvasEvaluator(GameProgram(definition), worker)
    snapshot = region_snapshot()
    before = deepcopy(snapshot.model_dump(mode="json"))
    result = evaluator.attempt(definition.initial, snapshot, GameStep(name="add", arguments={"amount": 2}))
    assert result.state == {"count": 2}
    assert result.model_state == {"count": 2}
    assert result.view[0] == {"kind": "text", "props": {"text": "42"}, "children": [], "action": None}
    assert set(result.actions) == {"clear"}
    assert result.canvas_effects == [{"op": "put_stamp", "target": {"page_id": "page-7"}, "key": "piece", "asset_id": "stamp:key", "center": {"x": 2000, "y": 5000}, "width": 600, "rotation": 0, "draggable": True}]
    assert definition.initial == {"count": 0}
    assert snapshot.model_dump(mode="json") == before


def test_refresh_uses_canvas_revision_without_changing_game_state(worker: GoWorker | None) -> None:
    definition = canvas_definition()
    evaluator = CanvasEvaluator(GameProgram(definition), worker)
    first = evaluator.prepare(definition.initial, CanvasSnapshot.empty())
    second = evaluator.prepare(definition.initial, region_snapshot())
    assert first.view[0]["props"] == {"text": "0"}
    assert first.actions == {}
    assert second.view[0]["props"] == {"text": "42"}
    assert set(second.actions) == {"clear"}
    assert first.state == second.state == {"count": 0}
    assert first.canvas_effects == second.canvas_effects == []


def test_guard_requires_bound_evidence(worker: GoWorker | None) -> None:
    definition = canvas_definition()
    evaluator = CanvasEvaluator(GameProgram(definition), worker)
    with pytest.raises(ValueError, match="not permitted"):
        evaluator.attempt(definition.initial, scene_snapshot(), GameStep(name="add", arguments={"amount": 1}))


def test_evidence_role_cannot_be_promoted(worker: GoWorker | None) -> None:
    definition = canvas_definition()
    evaluator = CanvasEvaluator(GameProgram(definition), worker)
    with pytest.raises(ValueError, match="evidence actor"):
        evaluator.attempt(definition.initial, region_snapshot(), GameStep(name="add", arguments={"amount": 1}), actor="contributor")


@pytest.mark.parametrize("effects", [
    '[{"op": "set_stamp_tray", "asset_ids": []}]',
    '[{"op": "remove_stamp", "target": {"page_id": "old-page"}, "key": "piece"}]',
    '{"op": "show_scene", "scene_id": "room", "page_ref": "a"}',
    '[{"op": "put_stamp", "target": {"page_id": canvas.page.id}, "key": "piece", "asset_id": "stamp:key", "center": {"x": 10001, "y": 0}, "width": 600, "rotation": 0, "draggable": true}]',
])
def test_invalid_output_rejects_the_candidate(worker: GoWorker | None, effects: str) -> None:
    definition = canvas_definition()
    definition.transitions["add"].canvas_effects = effects
    evaluator = CanvasEvaluator(GameProgram(definition), worker)
    with pytest.raises(ValueError):
        evaluator.attempt(definition.initial, region_snapshot(), GameStep(name="add", arguments={"amount": 1}))
    assert definition.initial == {"count": 0}


def test_effects_observe_old_state_validated_next_state_and_same_canvas(worker: GoWorker | None) -> None:
    definition = canvas_definition()
    definition.transitions["add"].canvas_effects = 'state.count == 0 && next_state.count == 2 && canvas.revision == 42 ? [{"op": "show_scene", "scene_id": "room", "page_ref": "arrival"}] : []'
    result = CanvasEvaluator(GameProgram(definition), worker).attempt(
        definition.initial, region_snapshot(), GameStep(name="add", arguments={"amount": 2}),
    )
    assert result.canvas_effects == [{"op": "show_scene", "scene_id": "room", "page_ref": "arrival"}]


def keyboard_definition() -> GameDefinition:
    definition = canvas_definition()
    definition.keyboard = KeyboardDefinition.model_validate({
        "enabled": "canvas.event == null && state.count < 10",
        "bindings": [{"id": "move", "key": "ArrowUp", "label": "Move up", "enabled": "canvas.page != null", "action": {"name": '"add"', "arguments": '{"amount": 1}', "confirmation": "Move?"}}],
    })
    return definition


def test_keyboard_resolution_uses_eventless_snapshot_and_existing_action_contract(worker: GoWorker | None) -> None:
    definition = keyboard_definition()
    evaluator = CanvasEvaluator(GameProgram(definition), worker)
    result = evaluator.prepare({"count": 0}, region_snapshot())
    assert [binding.model_dump(mode="json") for binding in result.shortcuts] == [{
        "id": "move", "key": "ArrowUp", "label": "Move up",
        "action": {"name": "add", "arguments": {"amount": 1}, "confirmation": "Move?", "permission": "orator"},
    }]
    assert evaluator.prepare({"count": 10}, region_snapshot()).shortcuts == []
    assert evaluator.prepare({"count": 0}, CanvasSnapshot.empty()).shortcuts == []


def test_keyboard_cannot_dispatch_narratron_only_action(worker: GoWorker | None) -> None:
    definition = keyboard_definition()
    assert definition.keyboard is not None
    definition.keyboard.bindings[0].action.name = '"clear"'
    with pytest.raises(ValueError, match="player/contributor"):
        CanvasEvaluator(GameProgram(definition), worker).prepare({"count": 0}, scene_snapshot())


def test_contributor_shortcut_permission_matches_panel_actions(worker: GoWorker | None) -> None:
    definition = keyboard_definition()
    assert definition.keyboard is not None
    definition.keyboard.bindings[0].action.permission = "contributor"
    definition.transitions["add"].guard = "state.count + action.amount <= 10"
    evaluator = CanvasEvaluator(GameProgram(definition), worker)
    step = GameStep(name="add", arguments={"amount": 1})
    assert evaluator.attempt({"count": 0}, scene_snapshot(), step, "contributor").state == {"count": 1}
    with pytest.raises(ValueError, match="active orator"):
        evaluator.attempt({"count": 0}, scene_snapshot(), GameStep(name="add", arguments={"amount": 2}), "contributor")
    definition.keyboard.enabled = "false"
    with pytest.raises(ValueError, match="active orator"):
        CanvasEvaluator(GameProgram(definition), worker).attempt({"count": 0}, scene_snapshot(), step, "contributor")


@pytest.mark.parametrize("location", ["invariant", "derived", "model_state", "function", "recovery"])
def test_canvas_access_does_not_leak_into_persisted_state_contracts(location: str) -> None:
    definition = canvas_definition()
    if location == "invariant":
        definition.invariants = ["canvas.page == null"]
    elif location == "derived":
        definition.derived = [Binding(name="page", expr="canvas.page")]
    elif location == "model_state":
        definition.model_state = "canvas"
    elif location == "function":
        definition.functions["hidden"] = FunctionDefinition(parameters=[], expr="canvas.page")
    else:
        from game_engine.game_runtime import GameErrorRecovery

        definition.transitions["add"].on_error = GameErrorRecovery(result="canvas")
    with pytest.raises(ValueError, match="independent of canvas|explicit canvas parameter"):
        GameProgram(definition)


def test_functions_can_receive_canvas_explicitly(worker: GoWorker | None) -> None:
    definition = canvas_definition()
    definition.functions["hasPage"] = FunctionDefinition(parameters=["canvas"], expr="canvas.page != null")
    definition.transitions["add"].guard = "hasPage(canvas)"
    result = CanvasEvaluator(GameProgram(definition), worker).attempt(
        definition.initial, scene_snapshot(), GameStep(name="add", arguments={"amount": 1}),
    )
    assert result.state == {"count": 1}


@pytest.mark.parametrize("name", ["canvas", "next_state"])
@pytest.mark.parametrize("location", ["derived", "transition", "view"])
def test_reserved_names_cannot_be_shadowed(name: str, location: str) -> None:
    definition = canvas_definition()
    if location == "derived":
        definition.derived = [Binding(name=name, expr="1")]
    elif location == "transition":
        definition.transitions["add"].bindings = [Binding(name=name, expr="1")]
    else:
        definition.view = ViewNode(kind="text", repeat="[1]", binding=name)
    with pytest.raises(ValueError, match="reserved"):
        GameProgram(definition)


@pytest.mark.parametrize("location", ["guard", "available", "view"])
def test_next_state_is_only_visible_to_output(location: str) -> None:
    definition = canvas_definition()
    if location == "view":
        definition.view.props = {"text": "next_state.count"}
    elif location == "guard":
        definition.transitions["add"].guard = "next_state.count == 1"
    else:
        definition.transitions["add"].available = "next_state.count == 1"
    with pytest.raises(ValueError, match="only in canvas_effects"):
        GameProgram(definition)


def test_canvas_opt_in_is_required_for_outputs_and_keyboard() -> None:
    definition = canvas_definition()
    definition.canvas_api = None
    with pytest.raises(ValueError, match="requires a canvas_api"):
        GameProgram(definition)
    definition.transitions["add"].canvas_effects = None
    definition.keyboard = KeyboardDefinition(bindings=[])
    with pytest.raises(ValueError, match="require a canvas_api"):
        GameProgram(definition)


def test_package_initial_validation_has_an_empty_canvas_and_stays_on_server() -> None:
    definition = keyboard_definition()
    definition.local_preview = True
    program = validate_game_package(definition.model_dump_json().encode("utf-8"))
    assert program.initial_view[0]["props"] == {"text": "0"}
    assert browser_program(program) is None


def test_legacy_action_path_cannot_silently_drop_canvas_commands(tmp_path: Path) -> None:
    path = tmp_path / "game"
    path.mkdir()
    (path / "game.json").write_text(canvas_definition().model_dump_json(), encoding="utf-8")
    persist = Mock()
    game = GameState(tmp_path, persist, Mock(), backend="python")
    assert game.program is not None
    with pytest.raises(ValueError, match="shared canvas commit host"):
        game.act(GameAction(name="add", revision=0, arguments={"amount": 1}))
    persist.assert_not_called()
    assert game.revision == 0


@pytest.mark.parametrize("keys", [["a", "A"], ["ArrowUp", "ArrowUp"]])
def test_keyboard_keys_are_unique_after_normalization(keys: list[str]) -> None:
    with pytest.raises(ValueError, match="unique after normalization"):
        KeyboardDefinition.model_validate({"bindings": [
            {"id": str(i), "key": key, "label": "Move", "action": {"name": '"add"'}} for i, key in enumerate(keys)
        ]})


def test_legacy_packages_can_still_use_canvas_as_an_ordinary_binding() -> None:
    definition = counter_definition()
    definition.derived.append(Binding(name="canvas", expr="1"))
    assert validate_game_package(definition.model_dump_json().encode("utf-8")).initial_model_state == {"count": 0}
