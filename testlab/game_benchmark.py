"""Shared trusted-checkers transaction workload for Python and CEL-Go."""
from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

from pydantic import JsonValue

from game.game_runtime import GameAction, GameStep
from game.game_state import GameState

GameFactory = Callable[[Path, Callable[[], None], Callable[..., None]], GameState]


def ignore_event(event: str) -> None:
    pass


def first_move(game: GameState) -> dict[str, JsonValue]:
    assert game.program is not None
    execution = game.program.execution()
    moves = execution.context(game.state)["moves"]
    assert type(moves) is list and moves
    move = moves[0]
    assert type(move) is dict
    return {"source": move["source"], "target": move["target"]}


def sample(directory: Path, game_factory: GameFactory = GameState) -> tuple[dict[str, float], dict[str, JsonValue]]:
    times: dict[str, float] = {}
    results: dict[str, JsonValue] = {}
    with TemporaryDirectory(prefix="narratron-cel-") as saved:
        path = Path(saved) / "game.json"
        game: GameState

        def persist() -> None:
            path.write_text(json.dumps(game.serialize()), encoding="utf-8")

        def measure(name: str, operation: Callable[[], None]) -> None:
            started = perf_counter()
            operation()
            times[name] = (perf_counter() - started) * 1000
            snapshot = game.story_snapshot("narratron")
            results[name] = {
                "state": game.state, "revision": game.revision,
                "payload": game.payload(), "story": snapshot, "saved": game.serialize(),
            }

        started = perf_counter()
        game = game_factory(directory, persist, ignore_event)
        times["load"] = (perf_counter() - started) * 1000
        if game.program is None:
            raise ValueError(f"Could not load benchmark game: {game.error}")
        results["load"] = {"payload": game.payload(), "story": game.story_snapshot("narratron")}
        arguments = first_move(game)

        def player() -> None:
            game.act(GameAction(name="move", arguments=arguments, revision=game.revision,
                                draft=[GameStep(name="select", arguments={"index": arguments["source"]})]))

        measure("player_with_draft", player)
        reply = first_move(game)

        def narratron() -> None:
            game.act(GameAction(name="narratron_move", arguments=reply, revision=game.revision), actor="narratron")

        measure("narratron", narratron)

        def invalid() -> None:
            before = game.serialize()
            try:
                with patch("game.game_state.logger.exception"):
                    game.act(GameAction(name="move", arguments={"source": 0, "target": 0}, revision=game.revision))
            except ValueError:
                assert game.serialize() == before
            else:
                raise AssertionError("Invalid move was accepted")

        measure("invalid_move", invalid)
        results["payload_bytes"] = len(json.dumps(game.payload()).encode("utf-8"))
        return times, results
