"""Replay player moves against a text provider using the production game runtime.

Run with ``uv run python -m testlab.game_harness --help``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from dotenv import load_dotenv
from pydantic import Field, JsonValue

from components.canvas.game_runtime import GameAction, GameModel, MAX_GAME_BYTES, validate_game_package
from components.canvas.game_state import GameState
from providers.text_response_provider import TextResponseProvider, TextResponseRequest

DEFAULT_GAME_MODEL = "gemini-3.5-flash-lite"


class Move(GameModel):
    name: str = Field(min_length=1, max_length=80)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class PlayerStep(GameModel):
    move: Move
    min_ai_actions: int | None = Field(default=None, ge=0, le=32)
    expected_state: dict[str, JsonValue] = Field(default_factory=dict)
    require_quiescent: bool = False


class GameScenario(GameModel):
    name: str = "player-moves"
    steps: list[PlayerStep] = Field(min_length=1, max_length=100)
    max_ai_actions: int = Field(default=8, ge=1, le=32)


class AIResponse(GameModel):
    response: str = Field(min_length=1, max_length=4000)
    action: Move | None


class TurnTrace(GameModel):
    player: Move
    before: dict[str, JsonValue]
    after_player: dict[str, JsonValue] = Field(default_factory=dict)
    prompts: list[dict[str, JsonValue]] = Field(default_factory=list)
    raw_responses: list[str] = Field(default_factory=list)
    responses: list[AIResponse] = Field(default_factory=list)
    ai_actions: list[GameAction] = Field(default_factory=list)
    after: dict[str, JsonValue] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    elapsed_seconds: float = 0.0


class GameReport(GameModel):
    scenario: str
    passed: bool = False
    turns: list[TurnTrace] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    final_state: dict[str, JsonValue] = Field(default_factory=dict)
    revision: int = 0


def no_persist() -> None:
    pass


def no_notify(event: str) -> None:
    pass


SYSTEM_INSTRUCTION = (
    "You are Narratron, responding to a player game move. Follow the game instructions. "
    "Use only currently available actions and legal arguments from the current game facts. "
    "Return JSON with response (a brief nonempty explanation) and action "
    "({name, arguments}, or null when you should stop). Take one action per response. "
    "Never invent state or act for the player. The next prompt will contain the committed state."
)


def play_turn(
    game: GameState,
    step: PlayerStep,
    provider: TextResponseProvider,
    model: str | None = None,
    max_ai_actions: int = 8,
    player_action: GameAction | None = None,
) -> TurnTrace:
    """Apply one player move and its AI reactions to an existing isolated game."""
    assert game.program is not None
    trace = TurnTrace(player=step.move, before=json.loads(json.dumps(game.state)))
    started = perf_counter()
    try:
        game.act(player_action or GameAction(**step.move.model_dump(), revision=game.revision))
        trace.after_player = json.loads(json.dumps(game.state))
        transition = game.program.definition.transitions[step.move.name]
        snapshot = game.story_snapshot("narratron")
        assert snapshot is not None
        available = bool(snapshot["actions"]) and transition.triggering
        required = step.min_ai_actions if step.min_ai_actions is not None else int(available)
        if available:
            # One extra response permits an explicit stop after the action budget.
            for _ in range(max_ai_actions + 1):
                snapshot = game.story_snapshot("narratron")
                assert snapshot is not None
                if not snapshot["actions"]:
                    break
                prompt: dict[str, JsonValue] = {
                    "player_move": step.move.model_dump(mode="json"),
                    "game": snapshot,
                    "previous_responses": [r.model_dump(mode="json") for r in trace.responses],
                }
                trace.prompts.append(prompt)
                action_schemas: list[JsonValue] = [
                    {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "enum": [name]},
                            "arguments": game.program.definition.transitions[name].parameters,
                        },
                        "required": ["name", "arguments"],
                        "additionalProperties": False,
                    }
                    for name in game.program.definition.transitions
                    if name in snapshot["actions"]
                ]
                action_schemas.append({"type": "null"})
                response_schema: dict[str, JsonValue] = {
                    "type": "object",
                    "properties": {
                        "response": {"type": "string"},
                        "action": {"anyOf": action_schemas},
                    },
                    "required": ["response", "action"],
                    "additionalProperties": False,
                }
                result = provider.generate(TextResponseRequest(
                    prompt=json.dumps(prompt, ensure_ascii=False),
                    system_instruction=SYSTEM_INSTRUCTION,
                    response_json_schema=response_schema,
                    temperature=0.0,
                    max_output_tokens=1500,
                    model=model,
                ))
                trace.raw_responses.append(result.text)
                # Validate raw JSON independently; a provider's parsed payload is not proof.
                response = AIResponse.model_validate_json(result.text)
                if not response.response.strip():
                    raise ValueError("AI explanation is blank.")
                trace.responses.append(response)
                if response.action is None:
                    break
                if len(trace.ai_actions) >= max_ai_actions:
                    raise ValueError("AI exceeded the per-turn action limit.")
                contracts = snapshot["actions"]
                if response.action.name not in contracts:
                    raise ValueError("AI chose an action that is not currently available.")
                action = GameAction(**response.action.model_dump(), revision=game.revision)
                game.act(action, actor="narratron")
                trace.ai_actions.append(action)
        if len(trace.ai_actions) < required:
            raise ValueError(f"Expected at least {required} AI actions; got {len(trace.ai_actions)}.")
        snapshot = game.story_snapshot("narratron")
        assert snapshot is not None
        if step.require_quiescent and snapshot["actions"]:
            raise ValueError("AI stopped while Narratron actions remain available.")
        for key, expected in step.expected_state.items():
            if key not in game.state or game.state[key] != expected:
                raise ValueError(f"Final state expectation failed for {key!r}.")
    except Exception as exc:
        trace.errors.append(str(exc))
    finally:
        trace.after = json.loads(json.dumps(game.state))
        trace.elapsed_seconds = perf_counter() - started
    return trace


def run_game_test(
    game_path: Path,
    scenario: GameScenario,
    provider: TextResponseProvider,
    model: str | None = None,
) -> GameReport:
    """Start a fresh temporary game; never modify the source package or theater saves.

    This tests model action selection directly, without the story planner scheduler.
    A failure stops the scenario, retaining all preceding committed moves in the trace.
    """
    report = GameReport(scenario=scenario.name)
    try:
        with game_path.open("rb") as source:
            content = source.read(MAX_GAME_BYTES + 1)
        validate_game_package(content)
        with TemporaryDirectory(prefix="narratron-game-lab-") as temporary:
            directory = Path(temporary)
            (directory / "game").mkdir()
            (directory / "game/game.json").write_bytes(content)
            for svg_file in game_path.parent.rglob("*.svg"):
                if svg_file.is_file():
                    target_svg = (directory / "game") / svg_file.relative_to(game_path.parent)
                    target_svg.parent.mkdir(parents=True, exist_ok=True)
                    target_svg.write_bytes(svg_file.read_bytes())
            game = GameState(directory, no_persist, no_notify)
            if game.program is None:
                raise ValueError(game.error)
            for step in scenario.steps:
                trace = play_turn(game, step, provider, model, scenario.max_ai_actions)
                report.turns.append(trace)
                if trace.errors:
                    report.errors.extend(trace.errors)
                    break
            report.final_state = json.loads(json.dumps(game.state))
            report.revision = game.revision
            if not report.errors and not any(turn.responses for turn in report.turns):
                report.errors.append("No AI responses were exercised. Use a triggering move that enables Narratron actions.")
    except Exception as exc:
        report.errors.append(str(exc))
    report.passed = not report.errors
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, required=True, help="Path to a version-2 game.json")
    parser.add_argument("--scenario", type=Path, required=True, help="JSON scenario of sequential player steps")
    parser.add_argument("--provider", default="gemini-3")
    parser.add_argument("--model", default=DEFAULT_GAME_MODEL)
    parser.add_argument("--output", type=Path, default=Path("output/game-test/report.json"))
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    from providers import get_text_response_provider

    try:
        scenario = GameScenario.model_validate_json(args.scenario.read_text(encoding="utf-8"))
        provider = get_text_response_provider(args.provider)
        report = run_game_test(args.game, scenario, provider, args.model)
    except Exception as exc:
        report = GameReport(scenario=str(args.scenario), errors=[str(exc)])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(f"{'PASS' if report.passed else 'FAIL'}: {report.scenario}; report: {args.output}")
    for error in report.errors:
        print(error)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
