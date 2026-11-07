"""Pure preparation of canvas-enabled game actions for a later host commit.

An evaluation produces a candidate and validated commands; it never persists or
publishes either. The canvas coordinator must commit both under one protocol.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
import json
from uuid import uuid4

from pydantic import Field, JsonValue

from game_engine.canvas_contract import Actor, CanvasSnapshot, commands_json, validate_effects
from game_engine.game_runtime import (
    BOOL_ADAPTER, MAP_ADAPTER, STRING_ADAPTER, Budget, Execution, GameModel,
    GameProgram, GameStep, MAX_MODEL_STATE_BYTES, MAX_VALUE_BYTES,
    RenderedAction, evaluate_with_recovery,
)

if TYPE_CHECKING:
    from game_engine.go_cel import GoWorker


class ResolvedShortcut(GameModel):
    id: str
    key: str
    label: str
    action: RenderedAction


class CanvasEvaluation(GameModel):
    state: dict[str, JsonValue]
    view: list[JsonValue]
    model_state: JsonValue
    actions: dict[str, JsonValue]
    shortcuts: list[ResolvedShortcut] = Field(default_factory=list)
    canvas_effects: list[JsonValue] = Field(default_factory=list)


def observation_context(snapshot: CanvasSnapshot) -> dict[str, JsonValue]:
    observed = snapshot.model_dump(mode="json")
    observed["event"] = None
    return observed


def resolve_shortcuts(execution: Execution, context: dict[str, JsonValue]) -> list[ResolvedShortcut]:
    keyboard = execution.program.definition.keyboard
    if keyboard is None or not BOOL_ADAPTER.validate_python(execution.evaluate(keyboard.enabled, context), strict=True):
        return []
    resolved: list[ResolvedShortcut] = []
    for binding in keyboard.bindings:
        if not BOOL_ADAPTER.validate_python(execution.evaluate(binding.enabled, context), strict=True):
            continue
        name = STRING_ADAPTER.validate_python(execution.evaluate(binding.action.name, context), strict=True)
        transition = execution.program.definition.transitions.get(name)
        if transition is None or not transition.is_player_actor:
            raise ValueError("Keyboard shortcuts must resolve to player/contributor transitions.")
        arguments = MAP_ADAPTER.validate_python(execution.evaluate(binding.action.arguments, context), strict=True)
        execution.program.action_validators[name].validate(arguments)
        action = RenderedAction(
            name=name, arguments=arguments, confirmation=binding.action.confirmation,
            permission=binding.action.permission or ("contributor" if transition.allows_contributor else "orator"),
        )
        if len(action.model_dump_json().encode("utf-8")) > 4096:
            raise ValueError("Keyboard action exceeds its 4 KiB size limit.")
        resolved.append(ResolvedShortcut(id=binding.id, key=binding.key, label=binding.label, action=action))
    return resolved


def check_evaluation_limits(evaluation: CanvasEvaluation, svg_expanded_bytes: int) -> None:
    if len(json.dumps(evaluation.state, ensure_ascii=False).encode("utf-8")) > MAX_VALUE_BYTES:
        raise ValueError("Game state exceeds its size budget.")
    view_bytes = len(json.dumps(evaluation.view, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    shortcut_bytes = len(json.dumps([binding.model_dump(mode="json") for binding in evaluation.shortcuts], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if view_bytes - svg_expanded_bytes + shortcut_bytes > MAX_VALUE_BYTES:
        raise ValueError("Rendered view and keyboard shortcuts exceed their size budget.")
    if len(json.dumps(evaluation.actions, ensure_ascii=False).encode("utf-8")) > MAX_VALUE_BYTES:
        raise ValueError("Model-facing game actions exceed their size budget.")


class CanvasEvaluator:
    def __init__(self, program: GameProgram, worker: GoWorker | None = None) -> None:
        if program.definition.canvas_api is None:
            raise ValueError("Canvas evaluation requires an opted-in game package.")
        self.program = program
        self.worker = worker
        self._package_key = uuid4().hex

    def prepare(self, state: dict[str, JsonValue], snapshot: CanvasSnapshot, *, username: str = "") -> CanvasEvaluation:
        snapshot = CanvasSnapshot.model_validate(snapshot.model_dump(mode="json"))
        if self.worker is not None:
            return self._go("prepare", state, snapshot, username=username)

        def prepare(budget: Budget) -> CanvasEvaluation:
            execution = self.program.execution(username=username)
            execution.budget = budget
            return self._prepare(execution, state, snapshot)

        return evaluate_with_recovery(prepare)

    def _prepare(self, execution: Execution, state: dict[str, JsonValue], snapshot: CanvasSnapshot) -> CanvasEvaluation:
        context = execution.context(state)
        execution.validate(state, context)
        view_context = {**context, "canvas": observation_context(snapshot)}
        execution.budget.nodes = 0
        svg_start = execution.svg_expanded_bytes
        view = [node.model_dump(mode="json") for node in execution.render(self.program.definition.view, view_context)]
        model_state, actions = execution.model_context(
            context, CanvasSnapshot.model_validate(observation_context(snapshot)),
        )
        shortcuts = resolve_shortcuts(execution, view_context)
        evaluation = CanvasEvaluation(state=state, view=view, model_state=model_state, actions=actions, shortcuts=shortcuts)
        check_evaluation_limits(evaluation, execution.svg_expanded_bytes - svg_start)
        return evaluation

    def attempt(
        self, state: dict[str, JsonValue], snapshot: CanvasSnapshot, step: GameStep, actor: Actor = "player",
        *, username: str = "",
    ) -> CanvasEvaluation:
        snapshot = CanvasSnapshot.model_validate(snapshot.model_dump(mode="json"))
        transition = self.program.definition.transitions.get(step.name)
        if transition is None or (actor == "narratron") != (transition.actor == "narratron"):
            raise ValueError("This actor cannot take that game action.")
        if len(step.model_dump_json().encode("utf-8")) > 4096:
            raise ValueError("Game action exceeds its size budget.")
        self.program.action_validators[step.name].validate(step.arguments)
        if snapshot.event is not None and snapshot.event.actor != actor:
            raise ValueError("Canvas evidence actor does not match the acting principal's role.")
        if self.worker is not None:
            return self._go("act", state, snapshot, step, actor, username=username)

        def attempt(budget: Budget) -> CanvasEvaluation:
            execution = self.program.execution(username=username)
            execution.budget = budget
            context = execution.context(state)
            if actor == "contributor" and not transition.allows_contributor:
                from game_engine.game_state import _view_nodes_allow_contributor
                from game_engine.game_runtime import GameAction

                view_context = {**context, "canvas": observation_context(snapshot)}
                view = [node.model_dump(mode="json") for node in execution.render(self.program.definition.view, view_context)]
                shortcut_allows = any(
                    binding.action.permission == "contributor"
                    and binding.action.name == step.name
                    and binding.action.arguments == step.arguments
                    for binding in resolve_shortcuts(execution, view_context)
                )
                if not shortcut_allows and not _view_nodes_allow_contributor(view, GameAction(name=step.name, arguments=step.arguments, revision=0)):
                    raise ValueError("Only the active orator can take this game action.")
            context = {**context, "canvas": snapshot.model_dump(mode="json"), "action": step.arguments}
            if not BOOL_ADAPTER.validate_python(execution.evaluate(transition.guard, context), strict=True):
                raise ValueError("This action is not permitted in the current game state.")
            execution.bindings(transition.bindings, context)
            candidate = MAP_ADAPTER.validate_python(execution.evaluate(transition.result, context), strict=True)
            execution.validate(candidate, execution.context(candidate))
            api = self.program.definition.canvas_api
            assert api is not None
            effects = commands_json(validate_effects(
                execution.evaluate(transition.canvas_effects, {**context, "next_state": candidate})
                if transition.canvas_effects is not None else [], api, snapshot,
            ))
            prepared = self._prepare(execution, candidate, snapshot)
            prepared.canvas_effects = effects
            return prepared

        return evaluate_with_recovery(attempt)

    def _go(
        self, op: str, state: dict[str, JsonValue], snapshot: CanvasSnapshot,
        step: GameStep | None = None, actor: Actor = "player", *, username: str = "",
    ) -> CanvasEvaluation:
        from game_engine.go_cel import Prepared, validate_prepared

        assert self.worker is not None
        if self._package_key not in self.worker.loaded_packages:
            self.worker.load(self.program, package=self._package_key)
        response = Prepared.model_validate(self.worker.request({
            "op": op, "state": state, "canvas": snapshot.model_dump(mode="json"),
            "steps": [step.model_dump(mode="json")] if step is not None else [], "actor": actor,
            "username": username,
            "package": self._package_key,
        }))
        view = validate_prepared(self.program, response)
        api = self.program.definition.canvas_api
        assert api is not None
        effects = commands_json(validate_effects(response.canvas_effects, api, snapshot))
        evaluation = CanvasEvaluation(
            state=response.state, view=view, model_state=response.model_state, actions=response.actions,
            shortcuts=[ResolvedShortcut.model_validate(entry) for entry in response.shortcuts], canvas_effects=effects,
        )
        for shortcut in evaluation.shortcuts:
            self.program.action_validators[shortcut.action.name].validate(shortcut.action.arguments)
            if len(shortcut.action.model_dump_json().encode("utf-8")) > 4096:
                raise ValueError("Keyboard action exceeds its 4 KiB size limit.")
        if self.program.definition.model_state is not None and len(json.dumps(evaluation.model_state, ensure_ascii=False).encode("utf-8")) > MAX_MODEL_STATE_BYTES:
            raise ValueError("Model-facing game state exceeds its size budget.")
        check_evaluation_limits(evaluation, response.svg_expanded_bytes)
        return evaluation
