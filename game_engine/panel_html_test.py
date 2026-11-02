from base64 import b64decode
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
    '<a href="https://external.test">link</a>',
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


@pytest.mark.parametrize("source", [
    "/local.svg", "../token.svg", "pieces/../../token.svg", "https://external.test/token.svg",
    "//external.test/token.svg", "data:image/svg+xml,test", "token.png", "token.svg?x=1",
    "pieces\\token.svg", "",
])
def test_panel_images_reject_non_asset_sources(source: str) -> None:
    with pytest.raises(ValueError):
        validate_panel_html(f'<img src="{source}">'.encode())


@pytest.mark.parametrize("html", [
    '<img>', '<img src="token.svg" src="other.svg">',
    '<img src="token.svg" onerror="alert(1)">', '<img src="token.svg" srcset="other.svg">',
])
def test_panel_images_reject_missing_duplicate_and_active_attributes(html: str) -> None:
    with pytest.raises(ValueError):
        validate_panel_html(html.encode())


@pytest.mark.parametrize("backend", ["python", "go"])
def test_panel_svg_assets_are_embedded_and_change_package_identity(tmp_path: Path, backend: Literal["python", "go"]) -> None:
    game_dir = tmp_path / "game"
    (game_dir / "pieces").mkdir(parents=True)
    (game_dir / "game.json").write_text(panel_definition().model_dump_json(exclude={"view"}), encoding="utf-8")
    (game_dir / "panel.html").write_text(
        '<img src="game/pieces/token.svg" alt="Token" width="32" height="32" />', encoding="utf-8",
    )
    asset = game_dir / "pieces/token.svg"
    asset.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><text x="1" y="20">A &amp; B</text></svg>', encoding="utf-8")
    game = GameState(tmp_path, Mock(), Mock(), backend=backend)
    assert not game.error
    assert game.program is not None
    html = game.program.panel_html
    assert html is not None
    svg = b64decode(html.split("base64,", 1)[1].split('"', 1)[0]).decode()
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg
    assert '<text x="1" y="20">A &amp; B</text>' in svg
    assert 'alt="Token" width="32" height="32"' in html
    asset.write_text('<svg viewBox="0 0 32 32"><circle r="10" /></svg>', encoding="utf-8")
    reloaded = GameState(tmp_path, Mock(), Mock(), backend=backend)
    assert not reloaded.error
    assert game.definition_hash != reloaded.definition_hash


def test_panel_svg_missing_assets_and_expansion_budget(tmp_path: Path) -> None:
    (tmp_path / "panel.html").write_text('<img src="missing.svg">', encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown panel SVG"):
        load_panel_html(tmp_path)
    (tmp_path / "large.svg").write_text('<svg><path d="' + "M0 0 " * 1900 + '" /></svg>', encoding="utf-8")
    (tmp_path / "panel.html").write_text('<img src="large.svg">' * 100, encoding="utf-8")
    with pytest.raises(ValueError, match="size budget"):
        load_panel_html(tmp_path)


def test_panel_svg_import_rejects_missing_asset_before_workspace_creation(tmp_path: Path) -> None:
    files = [
        ("game/game.json", panel_definition().model_dump_json(exclude={"view"}).encode()),
        ("game/panel.html", b'<img src="missing.svg">'),
    ]
    with pytest.raises(ValueError, match="Unknown panel SVG"):
        TheaterManager(tmp_path).create_theater("Invalid panel", "invalid", reference_files=files)
    assert not (tmp_path / "invalid").exists()


def test_panel_svg_import_preserves_nested_assets(tmp_path: Path) -> None:
    files = [
        ("game/panel.html", b'<img src="pieces/token.svg">'),
        ("game/game.json", panel_definition().model_dump_json(exclude={"view"}).encode()),
        ("game/pieces/token.svg", b'<svg viewBox="0 0 32 32"><circle r="10" /></svg>'),
    ]
    TheaterManager(tmp_path).create_theater("SVG panel", "svg-panel", reference_files=files)
    assert 'data:image/svg+xml;base64,' in load_panel_html(tmp_path / "svg-panel/game")


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
