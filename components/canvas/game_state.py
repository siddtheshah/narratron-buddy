"""Theater-defined game state, independent of story state and game concepts."""
from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import json
import logging
from pathlib import Path
from threading import RLock
from typing import Literal

from pydantic import Field, JsonValue, TypeAdapter

from components.canvas.game_runtime import (
    GameAction, GameModel, GameProgram, MAX_GAME_BYTES, MAX_VALUE_BYTES,
    validate_game_package,
)

logger = logging.getLogger(__name__)
GAME_FILE = "game/game.json"


class StoryGameCommit(GameModel):
    action: GameAction
    receipt: dict[str, JsonValue]
    pending: bool = False


class SavedGame(GameModel):
    definition_hash: str
    state: dict[str, JsonValue]
    revision: int = Field(ge=0)
    story_commits: dict[str, StoryGameCommit] = Field(default_factory=dict)


class StaleGameAction(ValueError):
    """The action was based on a state that has already changed."""


class UnauthorizedGameAction(ValueError):
    """The caller does not have permission to take this game action."""


def _view_nodes_allow_contributor(nodes: list[JsonValue], action: GameAction) -> bool:
    for item in nodes:
        if type(item) is not dict:
            continue
        node_action = item.get("action")
        if type(node_action) is dict:
            if (
                node_action.get("name") == action.name
                and node_action.get("permission") == "contributor"
                and node_action.get("arguments") == action.arguments
            ):
                return True
        children = item.get("children")
        if type(children) is list and _view_nodes_allow_contributor(children, action):
            return True
    return False


class GameState:
    def __init__(self, directory: Path, persist: Callable[[], None], notify: Callable[..., None]) -> None:
        self._lock = RLock()
        self._persist = persist
        self._notify = notify
        self.on_player_action: Callable[[GameAction, dict[str, JsonValue]], None] | None = None
        self.before_player_action: Callable[[], None] | None = None
        self.program: GameProgram | None = None
        self.state: dict[str, JsonValue] = {}
        self.revision = 0
        self.definition_hash = ""
        self.error = ""
        self._view: list[JsonValue] = []
        self._model_state: JsonValue = {}
        self._narratron_actions: dict[str, JsonValue] = {}
        self._story_commits: dict[str, StoryGameCommit] = {}
        path = directory / GAME_FILE
        try:
            if not path.exists():
                logger.debug("[GameState] No game package found at %s", path)
                return
            if not path.resolve().is_relative_to(directory.resolve()) or path.stat().st_size > MAX_GAME_BYTES:
                raise ValueError("Game package path or size is invalid.")
            content = path.read_bytes()
            self.program = validate_game_package(content, game_dir=path.parent)
            self.definition_hash = sha256(content).hexdigest()
            self.state = json.loads(json.dumps(self.program.definition.initial))
            self._view = json.loads(json.dumps(self.program.initial_view))
            self._model_state = json.loads(json.dumps(self.program.initial_model_state))
            self._narratron_actions = json.loads(json.dumps(self.program.initial_narratron_actions))
            logger.debug(
                "[GameState] Loaded game package from %s (definition_hash=%s, revision=%d, initial_state=%s)",
                path,
                self.definition_hash[:8],
                self.revision,
                self.state,
            )
        except Exception as exc:
            logger.warning("Game package failed to load: %s", exc, exc_info=True)
            self.error = "Game package could not be loaded. Check its declaration and CEL expressions."
            self.program = None

    def _prepare(self, state: dict[str, JsonValue]) -> list[JsonValue]:
        assert self.program is not None
        logger.debug("[GameState] Preparing state (revision=%d): %s", self.revision, state)
        if len(json.dumps(state, allow_nan=False).encode("utf-8")) > MAX_VALUE_BYTES:
            raise ValueError("Game state exceeds its size budget.")
        execution = self.program.execution()
        context = execution.context(state)
        execution.validate(state, context)
        view = [node.model_dump(mode="json") for node in execution.render(self.program.definition.view, context)]
        if len(json.dumps(view).encode("utf-8")) - execution.svg_expanded_bytes > MAX_VALUE_BYTES:
            raise ValueError("Rendered view exceeds its size budget.")
        model_state, actions = execution.model_context(context)
        self._view = view
        self._model_state = model_state
        self._narratron_actions = actions
        logger.debug(
            "[GameState] Prepared state successfully (nodes=%d, actions=%s)",
            len(view),
            list(actions.keys()),
        )
        return view

    def load(self, saved: JsonValue) -> None:
        if saved is None or self.program is None:
            logger.debug(
                "[GameState] Skipping load: saved_present=%s, program_present=%s",
                saved is not None,
                self.program is not None,
            )
            return
        with self._lock:
            logger.debug("[GameState] Loading saved game (current_revision=%d)", self.revision)
            previous_view, previous_model_state, previous_actions = self._view, self._model_state, self._narratron_actions
            try:
                # Inspect the hash before parsing the version-specific saved payload.
                data = TypeAdapter(dict[str, JsonValue]).validate_python(saved)
                if data.get("definition_hash") != self.definition_hash:
                    logger.debug(
                        "[GameState] Definition hash mismatch (saved=%s, current=%s); initializing fresh game",
                        data.get("definition_hash"),
                        self.definition_hash,
                    )
                    self.error = "Game definition changed; a fresh game was initialized."
                    return
                restored = SavedGame.model_validate(data)
                self._prepare(restored.state)
                self.state, self.revision = restored.state, restored.revision
                self._story_commits = restored.story_commits
                logger.debug(
                    "[GameState] Restored saved game to revision=%d (state=%s)",
                    self.revision,
                    self.state,
                )
            except Exception:
                # A broken game save must not interrupt loading story/chat state.
                self._view = previous_view
                self._model_state = previous_model_state
                self._narratron_actions = previous_actions
                self.error = "Saved game could not be loaded; the current game state was retained."
                logger.exception("Saved game failed to load")

    def serialize(self) -> dict[str, JsonValue]:
        with self._lock:
            if self.program is None:
                logger.debug("[GameState] Serializing empty game state (no program)")
                return {"game_state": None}
            logger.debug("[GameState] Serializing game state (revision=%d)", self.revision)
            return {"game_state": SavedGame(definition_hash=self.definition_hash, state=self.state, revision=self.revision, story_commits=self._story_commits).model_dump(mode="json")}

    def pending_story_actions(self) -> list[tuple[GameAction, dict[str, JsonValue]]]:
        """Recover player ingress even when a post-commit callback failed."""
        with self._lock:
            return [
                (commit.action.model_copy(deep=True), json.loads(json.dumps(commit.receipt)))
                for commit in self._story_commits.values() if commit.pending
            ]

    def story_action_commits(self, prefix: str) -> list[tuple[str, GameAction, dict[str, JsonValue]]]:
        with self._lock:
            return [
                (event_id, commit.action.model_copy(deep=True), json.loads(json.dumps(commit.receipt)))
                for event_id, commit in self._story_commits.items() if event_id.startswith(prefix)
            ]

    def acknowledge_story_action(self, event_id: str) -> None:
        with self._lock:
            commit = self._story_commits[event_id]
            if commit.pending:
                commit.pending = False
                try:
                    self._persist()
                except Exception:
                    commit.pending = True
                    raise

    def commit_story_action(self, action: GameAction, event_id: str) -> dict[str, JsonValue]:
        """Idempotently commit and return the receipt captured at the commit boundary."""
        with self._lock:
            existing = self._story_commits.get(event_id)
            if existing is not None:
                if existing.action != action:
                    raise ValueError("Game action identity reused with different content.")
                return json.loads(json.dumps(existing.receipt))
            self.act(action, actor="narratron", story_event_id=event_id)
            return json.loads(json.dumps(self._story_commits[event_id].receipt))

    def payload(self) -> dict[str, JsonValue] | None:
        with self._lock:
            if self.program is None:
                return {"error": self.error} if self.error else None
            return {"definition_hash": self.definition_hash, "revision": self.revision, "view": json.loads(json.dumps(self._view)), "error": self.error}

    def story_snapshot(self, audience: Literal["responder", "narratron"]) -> dict[str, JsonValue] | None:
        """Give the responder facts and the planner its package-owned instructions."""
        with self._lock:
            if self.program is None:
                logger.debug("[GameState] Story snapshot requested for audience '%s' but no program is loaded", audience)
                return None
            logger.debug("[GameState] Generating story snapshot for audience '%s' at revision=%d", audience, self.revision)
            snapshot: dict[str, JsonValue] = {"revision": self.revision, "state": self._model_state}
            if audience == "narratron":
                snapshot["instructions"] = self.program.definition.instructions
                snapshot["actions"] = self._narratron_actions
            return json.loads(json.dumps(snapshot))

    def report_error(self, message: str) -> None:
        """Publish a game-only diagnostic without changing the playable state."""
        with self._lock:
            logger.debug("[GameState] Reporting error (revision=%d): %s", self.revision, message)
            self.error = message
            self._notify_safely()

    def _notify_safely(self) -> None:
        try:
            logger.debug("[GameState] Notifying canvas listener at revision=%d", self.revision)
            self._notify("latest")
        except Exception:
            logger.exception("Game update notification failed at revision %s", self.revision)

    def act(
        self, action: GameAction, actor: Literal["player", "contributor", "narratron"] = "player",
        *, story_event_id: str = "",
    ) -> dict[str, JsonValue] | None:
        if actor in ("player", "contributor") and self.before_player_action is not None:
            self.before_player_action()
        with self._lock:
            if self.program is None:
                logger.debug("[GameState] Act rejected: no playable game")
                raise ValueError("This theater has no playable game.")
            if action.revision != self.revision:
                logger.debug(
                    "[GameState] Act rejected: stale revision (action_revision=%d, current_revision=%d)",
                    action.revision,
                    self.revision,
                )
                raise StaleGameAction("The game changed. Review its current state and try again.")
            if len(action.model_dump_json().encode("utf-8")) > 4096:
                logger.debug("[GameState] Act rejected: action exceeds size budget")
                raise ValueError("Game action exceeds its size budget.")
            transition = self.program.definition.transitions.get(action.name)
            if transition is None:
                logger.debug("[GameState] Act rejected: unknown action '%s'", action.name)
                raise ValueError("Unknown game action.")
            if actor == "narratron":
                if transition.actor != "narratron":
                    logger.debug(
                        "[GameState] Act rejected: actor mismatch for '%s' (action actor='%s', required actor='%s')",
                        action.name,
                        actor,
                        transition.actor,
                    )
                    raise ValueError("This actor cannot take that game action.")
            else:
                if not transition.is_player_actor:
                    logger.debug(
                        "[GameState] Act rejected: actor mismatch for '%s' (action actor='%s', required actor='%s')",
                        action.name,
                        actor,
                        transition.actor,
                    )
                    raise ValueError("This actor cannot take that game action.")
                if actor == "contributor" and not (
                    transition.allows_contributor or _view_nodes_allow_contributor(self._view, action)
                ):
                    logger.debug(
                        "[GameState] Act rejected: contributor permission required for '%s'",
                        action.name,
                    )
                    raise UnauthorizedGameAction("Only the active orator can take this game action.")
            logger.debug(
                "[GameState] Executing action '%s' by actor '%s' at revision=%d (arguments=%s)",
                action.name,
                actor,
                self.revision,
                action.arguments,
            )
            previous, previous_view, previous_model_state, previous_actions, revision = (
                self.state, self._view, self._model_state, self._narratron_actions, self.revision
            )
            event_id = story_event_id or f"player:{self.definition_hash}:{action.revision}"
            try:
                self.program.action_validators[action.name].validate(action.arguments)
                execution = self.program.execution()
                context = execution.context(self.state)
                context["action"] = action.arguments
                if not TypeAdapter(bool).validate_python(execution.evaluate(transition.guard, context), strict=True):
                    logger.debug("[GameState] Guard condition failed for action '%s'", action.name)
                    raise ValueError("This action is not permitted in the current game state.")
                execution.bindings(transition.bindings, context)
                candidate = TypeAdapter(dict[str, JsonValue]).validate_python(execution.evaluate(transition.result, context), strict=True)
                # Evaluate the complete view before publishing or persisting anything.
                self._prepare(candidate)
                self.state = candidate
                self.revision += 1
                snapshot = self.story_snapshot("narratron")
                assert snapshot is not None
                snapshot["story_event_id"] = event_id
                self._story_commits[event_id] = StoryGameCommit(
                    action=action, receipt=snapshot,
                    pending=actor in ("player", "contributor") and transition.triggering,
                )
                self._persist()
                logger.debug(
                    "[GameState] Action '%s' applied: revision advanced to %d, new state=%s",
                    action.name,
                    self.revision,
                    self.state,
                )
            except Exception as exc:
                self._story_commits.pop(event_id, None)
                self.state, self._view, self._model_state, self._narratron_actions, self.revision = (
                    previous, previous_view, previous_model_state, previous_actions, revision
                )
                logger.exception("Game action failed: %s", action.name)
                self.report_error("Game action failed; the previous game state was retained.")
                raise ValueError(str(exc)[:250]) from exc
            self.error = ""
            self._notify_safely()
            if actor in ("player", "contributor") and transition.triggering and self.on_player_action is not None:
                try:
                    logger.debug(
                        "[GameState] Invoking on_player_action callback for action '%s' at revision=%d",
                        action.name,
                        self.revision,
                    )
                    self.on_player_action(action, snapshot)
                except Exception:
                    logger.exception("Player game action callback failed at revision %s", self.revision)
            return self.payload()
