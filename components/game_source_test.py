"""Human-authored YAML uses the same game contract and runtime as JSON."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from components.canvas.game_runtime import MAX_GAME_BYTES, validate_game_package
from components.canvas.game_state import GameAction, GameState
from components.game_source import game_source_json
from testing.game_fixtures import counter_definition


@pytest.mark.parametrize("filename", ["game.yaml", "game.yml"])
def test_yaml_comments_and_multiline_rules_play_and_restore(tmp_path: Path, filename: str) -> None:
    definition = counter_definition()
    source = yaml.safe_dump(definition.model_dump(mode="json"), sort_keys=False)
    source = source.replace("result: '{\"count\": state.count + action.amount}'", '''result: |-
      {"count":
        state.count + action.amount}''')
    assert "result: |-" in source
    directory = tmp_path / "game"
    directory.mkdir()
    (directory / filename).write_text("# A counter anyone can edit\n" + source, encoding="utf-8")
    game = GameState(tmp_path, Mock(), Mock())
    assert game.error == ""
    game.act(GameAction(name="add", arguments={"amount": 3}, revision=0))
    restored = GameState(tmp_path, Mock(), Mock())
    restored.load(game.serialize()["game_state"])
    assert restored.state == {"count": 3}
    assert restored.revision == 1
    assert restored.payload()["view"] == game.payload()["view"]


def test_yaml_preserves_words_dates_and_boolean_literals() -> None:
    parsed = json.loads(game_source_json(b"on: yes\noff: no\ndate: 2026-10-21\nflag: false\n", "game.yaml"))
    assert parsed == {"on": "yes", "off": "no", "date": "2026-10-21", "flag": False}
    # Other configuration loaders retain their existing behavior.
    assert yaml.safe_load("flag: yes") == {"flag": True}


@pytest.mark.parametrize("source", [
    "version: 2\nversion: 2",
    "initial: {count: 0, count: 1}",
    "initial: {1: value}",
    "initial: !!python/object/apply:builtins.print [unsafe]",
    "initial: &cycle [*cycle]",
    "initial: &shared {}\nconstants: *shared",
    "initial: {number: .nan}",
    "initial: {number: .inf}",
    "initial: [unterminated",
    "---\ninitial: {}\n---\ninitial: {}",
    "initial: " + "[" * 65 + "0" + "]" * 65,
    "[]",
])
def test_invalid_yaml_is_rejected_by_runtime_and_editor(source: str) -> None:
    from services.theater_builder import validate_text

    with pytest.raises((ValueError, yaml.YAMLError)):
        game_source_json(source.encode("utf-8"), "game.yaml")
    with pytest.raises(ValueError):
        validate_game_package(source.encode("utf-8"), filename="game.yaml")
    with pytest.raises(ValueError):
        validate_text("game/game.yaml", source)


def test_yaml_size_and_existing_game_schema_limits_are_enforced() -> None:
    with pytest.raises(ValueError, match="size limit"):
        validate_game_package(b"#" * (MAX_GAME_BYTES + 1), filename="game.yaml")
    with pytest.raises(ValueError, match="size limit after decoding"):
        validate_game_package(b"initial: {data: '" + b'"' * 65_536 + b"'}", filename="game.yaml")
    definition = counter_definition().model_dump(mode="json")
    definition["initial"] = {"count": -1}
    with pytest.raises(ValueError, match="Invalid game package"):
        validate_game_package(yaml.safe_dump(definition).encode("utf-8"), filename="game.yaml")


def test_ambiguous_game_files_report_a_game_only_error(tmp_path: Path) -> None:
    directory = tmp_path / "game"
    directory.mkdir()
    content = counter_definition().model_dump_json()
    (directory / "game.json").write_text(content, encoding="utf-8")
    (directory / "game.yaml").write_text(content, encoding="utf-8")
    game = GameState(tmp_path, Mock(), Mock())
    assert game.program is None
    assert game.error
