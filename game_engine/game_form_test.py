"""Input strings are bounded before action execution, including replayed drafts."""
import pytest
from pydantic import JsonValue, ValidationError

from game_engine.game_runtime import GameAction, GameStep


@pytest.mark.parametrize("arguments", [
    {"text": "a" * 1000},
    {"nested": [{"text": "a" * 1000}]},
    {"n": 12, "f": 1.5, "b": True, "v": None},
])
def test_action_accepts_bounded_strings(arguments: dict[str, JsonValue]) -> None:
    action = GameAction(name="submit", arguments=arguments, revision=0)
    assert action.arguments == arguments


@pytest.mark.parametrize("arguments", [
    {"text": "a" * 1001},
    {"nested": [{"text": "a" * 1001}]},
    {"a" * 1001: "value"},
    {"nested": {"a" * 1001: "value"}},
])
def test_action_rejects_oversized_strings(arguments: dict[str, JsonValue]) -> None:
    with pytest.raises(ValidationError):
        GameAction(name="submit", arguments=arguments, revision=0)
    with pytest.raises(ValidationError):
        GameStep(name="submit", arguments=arguments)
    with pytest.raises(ValidationError):
        GameAction.model_validate({
            "name": "submit", "revision": 0,
            "draft": [{"name": "edit", "arguments": arguments}],
        })
