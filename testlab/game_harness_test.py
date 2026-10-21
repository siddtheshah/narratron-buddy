import json
from pathlib import Path
from unittest.mock import create_autospec

import pytest

from providers.text_response_provider import TextResponseProvider, TextResponseResult
from testlab.game_harness import GameScenario, run_game_test

ROOT = Path(__file__).resolve().parent.parent
GAME = ROOT / "adventures/example_adventure/game/game.yaml"


def scenario() -> GameScenario:
    return GameScenario.model_validate_json((ROOT / "testlab/scenarios/checkers.json").read_text(encoding="utf-8"))


def response(name: str | None, arguments: dict[str, int] | None = None) -> TextResponseResult:
    return TextResponseResult(
        text=json.dumps({"response": "I respond to the move.", "action": {"name": name, "arguments": arguments or {}} if name else None}),
        provider="test", model="test",
    )


def test_checkers_replays_selection_and_valid_ai_response_without_modifying_source() -> None:
    original = GAME.read_bytes()
    provider = create_autospec(TextResponseProvider, instance=True)
    provider.generate.return_value = response("narratron_move", {"source": 17, "target": 24})
    report = run_game_test(GAME, scenario(), provider)
    assert report.passed, report.errors
    assert report.revision == 3
    assert report.final_state["turn"] == 1
    assert report.final_state["board"][24] == -1
    assert report.turns[0].responses == []
    assert report.turns[1].ai_actions[0].revision == 2
    assert GAME.read_bytes() == original
    provider.generate.assert_called_once()
    prompt = json.loads(provider.generate.call_args.args[0].prompt)
    assert prompt["game"]["state"]["turn"] == -1
    assert "narratron_move" in prompt["game"]["actions"]
    assert prompt["player_move"]["arguments"] == {"source": 40, "target": 33}


@pytest.mark.parametrize("result", [
    response("move", {"source": 17, "target": 24}),
    response("narratron_move", {"source": 17, "target": 63}),
    response("narratron_move", {"source": 17}),
    response(None),
    TextResponseResult(text="not JSON", provider="test", model="test"),
    TextResponseResult(text='{"response":"  ","action":null}', provider="test", model="test"),
])
def test_invalid_ai_response_fails_and_preserves_player_state(result: TextResponseResult) -> None:
    provider = create_autospec(TextResponseProvider, instance=True)
    provider.generate.return_value = result
    report = run_game_test(GAME, scenario(), provider)
    assert not report.passed
    assert report.errors
    assert report.revision == 2
    assert report.final_state == report.turns[1].after_player
    assert report.turns[1].raw_responses == [result.text]


def test_provider_failure_is_recorded() -> None:
    provider = create_autospec(TextResponseProvider, instance=True)
    provider.generate.side_effect = RuntimeError("Provider unavailable")
    report = run_game_test(GAME, scenario(), provider)
    assert not report.passed
    assert report.errors == ["Provider unavailable"]
    assert report.revision == 2


def test_illegal_player_move_never_calls_provider() -> None:
    provider = create_autospec(TextResponseProvider, instance=True)
    fixture = GameScenario.model_validate({"steps": [{"move": {"name": "move", "arguments": {"source": 40, "target": 0}}}]})
    report = run_game_test(GAME, fixture, provider)
    assert not report.passed
    assert report.revision == 0
    provider.generate.assert_not_called()


def test_only_nontriggering_selection_does_not_vacuously_pass() -> None:
    provider = create_autospec(TextResponseProvider, instance=True)
    fixture = scenario()
    fixture.steps = fixture.steps[:1]
    report = run_game_test(GAME, fixture, provider)
    assert not report.passed
    assert "No AI responses" in report.errors[0]
    provider.generate.assert_not_called()


def test_state_expectations_detect_wrong_outcome() -> None:
    provider = create_autospec(TextResponseProvider, instance=True)
    provider.generate.return_value = response("narratron_move", {"source": 17, "target": 24})
    fixture = scenario()
    fixture.steps[1].expected_state = {"winner": 1}
    report = run_game_test(GAME, fixture, provider)
    assert not report.passed
    assert "expectation failed" in report.errors[0]


def test_missing_package_fails_without_provider_call(tmp_path: Path) -> None:
    provider = create_autospec(TextResponseProvider, instance=True)
    report = run_game_test(tmp_path / "game.json", scenario(), provider)
    assert not report.passed
    provider.generate.assert_not_called()


def test_repeated_ai_actions_are_bounded_and_receive_updated_state(tmp_path: Path) -> None:
    from testing.game_fixtures import counter_definition

    definition = counter_definition()
    definition.transitions["clear"].actor = "narratron"
    game = tmp_path / "game.json"
    game.write_text(definition.model_dump_json(), encoding="utf-8")
    fixture = GameScenario.model_validate({"max_ai_actions": 2, "steps": [{"move": {"name": "add", "arguments": {"amount": 1}}}]})
    provider = create_autospec(TextResponseProvider, instance=True)
    provider.generate.return_value = response("clear")
    report = run_game_test(game, fixture, provider)
    assert not report.passed
    assert report.errors == ["AI exceeded the per-turn action limit."]
    assert len(report.turns[0].ai_actions) == 2
    prompts = [json.loads(call.args[0].prompt) for call in provider.generate.call_args_list]
    assert [prompt["game"]["revision"] for prompt in prompts] == [1, 2, 3]
    assert prompts[1]["game"]["state"] == {"count": 0}


def test_quiescent_expectation_rejects_early_stop(tmp_path: Path) -> None:
    from testing.game_fixtures import counter_definition

    definition = counter_definition()
    definition.transitions["clear"].actor = "narratron"
    game = tmp_path / "game.json"
    game.write_text(definition.model_dump_json(), encoding="utf-8")
    fixture = GameScenario.model_validate({"steps": [{"move": {"name": "add", "arguments": {"amount": 1}}, "require_quiescent": True}]})
    provider = create_autospec(TextResponseProvider, instance=True)
    provider.generate.side_effect = [response("clear"), response(None)]
    report = run_game_test(game, fixture, provider)
    assert not report.passed
    assert "remain available" in report.errors[0]
