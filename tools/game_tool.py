"""Live-agent access to a theater's authoritative game runtime."""

import json

from pydantic import JsonValue, TypeAdapter

from components.canvas.game_runtime import GameAction
from components.canvas.game_state import GameState


class GameTool:
    """Read package-owned context and play only as Narratron."""

    def __init__(self, game: GameState) -> None:
        self.game = game

    def _snapshot(self) -> dict[str, JsonValue]:
        snapshot = self.game.story_snapshot("narratron")
        if snapshot is None:
            raise ValueError("This theater has no playable game.")
        return snapshot

    def view_game_instructions(self) -> str:
        """Read the authored game rules and instructions before playing."""
        return json.dumps({"instructions": self._snapshot()["instructions"]}, ensure_ascii=False)

    def view_game_state(self) -> str:
        """Read the current model-facing game state and its revision."""
        snapshot = self._snapshot()
        return json.dumps({"revision": snapshot["revision"], "state": snapshot["state"]}, ensure_ascii=False)

    def get_available_actions(self) -> str:
        """List currently available Narratron actions with descriptions, argument schemas, and revision."""
        snapshot = self._snapshot()
        return json.dumps({"revision": snapshot["revision"], "actions": snapshot["actions"]}, ensure_ascii=False)

    def take_game_action(self, name: str, arguments_json: str, revision: int) -> str:
        """Play a Narratron action using a JSON argument mapping and the last observed revision.

        Read instructions, state, and available actions first. On a stale revision,
        inspect the current state before choosing again. Player actions belong to
        the player. The returned snapshot includes the new revision and actions.
        """
        try:
            action = GameAction(
                name=name,
                arguments=TypeAdapter(dict[str, JsonValue]).validate_json(arguments_json),
                revision=revision,
            )
            self.game.act(action, actor="narratron")
            return json.dumps(self._snapshot(), ensure_ascii=False)
        except ValueError as exc:
            return f"Error: {exc}"
