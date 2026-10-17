"""Theater-defined game state, independent of story state and game concepts."""
from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import json
import logging
from pathlib import Path
from threading import RLock

from pydantic import Field, JsonValue, TypeAdapter

from components.canvas.game_runtime import (
    GameAction, GameModel, GameProgram, MAX_GAME_BYTES, MAX_VALUE_BYTES,
    validate_game_package,
)

logger = logging.getLogger(__name__)
GAME_FILE = "game/game.json"


class SavedGame(GameModel):
    definition_hash: str
    state: dict[str, JsonValue]
    revision: int = Field(ge=0)


class StaleGameAction(ValueError):
    """The action was based on a state that has already changed."""


class GameState:
    def __init__(self, directory: Path, persist: Callable[[], None], notify: Callable[..., None]) -> None:
        self._lock = RLock()
        self._persist = persist
        self._notify = notify
        self.program: GameProgram | None = None
        self.state: dict[str, JsonValue] = {}
        self.revision = 0
        self.definition_hash = ""
        self.error = ""
        self._view: list[JsonValue] = []
        path = directory / GAME_FILE
        if not path.exists():
            return
        try:
            if not path.resolve().is_relative_to(directory.resolve()) or path.stat().st_size > MAX_GAME_BYTES:
                raise ValueError("Game package path or size is invalid.")
            content = path.read_bytes()
            self.program = validate_game_package(content)
            self.definition_hash = sha256(content).hexdigest()
            self.state = json.loads(json.dumps(self.program.definition.initial))
            self._view = json.loads(json.dumps(self.program.initial_view))
        except Exception as exc:
            logger.warning("Game package failed to load: %s", exc)
            self.error = "Game package could not be loaded. Check its declaration and CEL expressions."
            self.program = None

    def _prepare(self, state: dict[str, JsonValue]) -> list[JsonValue]:
        assert self.program is not None
        if len(json.dumps(state, allow_nan=False).encode("utf-8")) > MAX_VALUE_BYTES:
            raise ValueError("Game state exceeds its size budget.")
        execution = self.program.execution()
        context = execution.context(state)
        execution.validate(state, context)
        view = [node.model_dump(mode="json") for node in execution.render(self.program.definition.view, context)]
        if len(json.dumps(view).encode("utf-8")) > MAX_VALUE_BYTES:
            raise ValueError("Rendered view exceeds its size budget.")
        self._view = view
        return view

    def load(self, saved: JsonValue) -> None:
        if saved is None or self.program is None:
            return
        with self._lock:
            # Inspect the hash before parsing the version-specific saved payload.
            data = TypeAdapter(dict[str, JsonValue]).validate_python(saved)
            if data.get("definition_hash") != self.definition_hash:
                self.error = "Game definition changed; a fresh game was initialized."
                return
            restored = SavedGame.model_validate(data)
            self._prepare(restored.state)
            self.state, self.revision = restored.state, restored.revision

    def serialize(self) -> dict[str, JsonValue]:
        with self._lock:
            if self.program is None:
                return {"game_state": None}
            return {"game_state": SavedGame(definition_hash=self.definition_hash, state=self.state, revision=self.revision).model_dump(mode="json")}

    def payload(self) -> dict[str, JsonValue] | None:
        with self._lock:
            if self.program is None:
                return {"error": self.error} if self.error else None
            return {"definition_hash": self.definition_hash, "revision": self.revision, "view": json.loads(json.dumps(self._view)), "error": self.error}

    def act(self, action: GameAction) -> dict[str, JsonValue] | None:
        with self._lock:
            if self.program is None:
                raise ValueError("This theater has no playable game.")
            if action.revision != self.revision:
                raise StaleGameAction("The game changed. Review its current state and try again.")
            if len(action.model_dump_json().encode("utf-8")) > 4096:
                raise ValueError("Game action exceeds its size budget.")
            transition = self.program.definition.transitions.get(action.name)
            if transition is None:
                raise ValueError("Unknown game action.")
            previous, previous_view, revision = self.state, self._view, self.revision
            try:
                self.program.action_validators[action.name].validate(action.arguments)
                execution = self.program.execution()
                context = execution.context(self.state)
                context["action"] = action.arguments
                if not TypeAdapter(bool).validate_python(execution.evaluate(transition.guard, context), strict=True):
                    raise ValueError("This action is not permitted in the current game state.")
                execution.bindings(transition.bindings, context)
                candidate = TypeAdapter(dict[str, JsonValue]).validate_python(execution.evaluate(transition.result, context), strict=True)
                # Evaluate the complete view before publishing or persisting anything.
                self._prepare(candidate)
                self.state = candidate
                self.revision += 1
                self._persist()
            except Exception as exc:
                self.state, self._view, self.revision = previous, previous_view, revision
                raise ValueError(str(exc)[:250]) from exc
            self._notify("latest")
            return self.payload()
