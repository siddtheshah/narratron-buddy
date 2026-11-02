from pathlib import Path
from typing import Literal
from unittest.mock import Mock

import pytest

from game_engine.game_runtime import GameAction, GameDefinition, validate_game_package
from game_engine.game_state import GameState, UnauthorizedGameAction
from game_engine.panel_html import MAX_PANEL_BYTES, load_panel_html, validate_panel_html
from components.theater_manager import TheaterManager
from testing.game_fixtures import counter_definition, panel_definition


@pytest.mark.parametrize("html", [
    '<a href="https://external.test">link</a>', '<img src="/local.png">',
    '<script>alert(1)</script>', '<div onclick="alert(1)">x</div>',
    '<iframe srcdoc="x"></iframe>', '<meta http-equiv="refresh" content="0;url=https://external.test">',
    '<base href="https://external.test">', '<form action="/submit"></form>',
    '<svg><a href="https://external.test">x</a></svg>',
    '<link rel="stylesheet" href="/style.css">', '<button formaction="/x">x</button>',
    '<div background="/image.png">x</div>',
])
def test_rejects_active_and_resource_html(html: str) -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        validate_panel_html(html.encode())


def test_html_budgets_and_encoding(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="size budget"):
        validate_panel_html(b"x" * (MAX_PANEL_BYTES + 1))
    with pytest.raises(ValueError):
        validate_panel_html(b"\xff")
    with pytest.raises(ValueError, match="element budget"):
        validate_panel_html(b"<br>" * 2049)
    with pytest.raises(FileNotFoundError):
        load_panel_html(tmp_path)


@pytest.mark.parametrize("backend", ["python", "go"])
def test_panel_parameters_and_actions_update_on_both_backends(tmp_path: Path, backend: Literal["python", "go"]) -> None:
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    definition = panel_definition()
    (game_dir / "game.json").write_text(definition.model_dump_json(), encoding="utf-8")
    html = '<style>p {color: red}</style><p data-panel-text="count"></p><button data-panel-action="increment">Add</button>'
    (game_dir / "panel.html").write_text(html, encoding="utf-8")
    game = GameState(tmp_path, Mock(), Mock(), backend=backend)
    assert game.error == ""
    payload = game.payload()
    assert payload is not None
    assert payload["panel_html"] == html
    assert game._view[0]["props"] == {"count": 0, "doubled": 0, "full": False}
    with pytest.raises(UnauthorizedGameAction):
        game.act(GameAction(name="add", arguments={"amount": 1}, revision=0), actor="contributor")
    game.act(GameAction(name="add", arguments={"amount": 1}, revision=0))
    assert game._view[0]["props"] == {"count": 1, "doubled": 2, "full": False}
    assert game._view[0]["children"][0]["action"]["name"] == "add"
    original_hash = game.definition_hash
    (game_dir / "panel.html").write_text(html + "<p>Updated</p>", encoding="utf-8")
    reloaded = GameState(tmp_path, Mock(), Mock(), backend="python")
    assert reloaded.definition_hash != original_hash


def test_panel_requires_file_and_excludes_structured_view(tmp_path: Path) -> None:
    definition = panel_definition()
    with pytest.raises(ValueError, match="panel.html"):
        validate_game_package(definition.model_dump_json().encode(), game_dir=tmp_path)
    data = counter_definition().model_dump(mode="json")
    data["panel"] = {"parameters": {}}
    with pytest.raises(ValueError, match="not both"):
        GameDefinition.model_validate(data)


@pytest.mark.parametrize("content", [None, b'<script src="https://external.test/script.js"></script>'])
def test_panel_import_validation_precedes_workspace_creation(tmp_path: Path, content: bytes | None) -> None:
    files = [("game/game.json", panel_definition().model_dump_json().encode())]
    if content is not None:
        files.append(("game/panel.html", content))
    with pytest.raises(ValueError):
        TheaterManager(tmp_path).create_theater("Invalid panel", "invalid", reference_files=files)
    assert not (tmp_path / "invalid").exists()


@pytest.mark.parametrize("backend", ["python", "go"])
def test_named_panel_action_retains_explicit_contributor_permission(tmp_path: Path, backend: Literal["python", "go"]) -> None:
    definition = panel_definition()
    assert definition.panel is not None
    definition.panel.actions["increment"].permission = "contributor"
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "game.json").write_text(definition.model_dump_json(exclude={"view"}), encoding="utf-8")
    (game_dir / "panel.html").write_text('<button data-panel-action="increment">Add</button>', encoding="utf-8")
    game = GameState(tmp_path, Mock(), Mock(), backend=backend)
    assert not game.error
    game.act(GameAction(name="add", arguments={"amount": 1}, revision=0), actor="contributor")
    assert game.state == {"count": 1}
