from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
from unittest.mock import Mock

import pytest

from components.canvas.canvas_state_manager import CanvasStateManager
from components.theater_manager import TheaterManager, extract_asset_package
from services.adventure_service import AdventureService
from services.theater_builder import safe_asset_path, upload_path, validate_text
from components.canvas.game_state import GameAction, GameState


def test_game_survives_adventure_mount_and_canvas_reload(tmp_path: Path) -> None:
    references, playlists, lore, config = AdventureService(Path("adventures")).load_adventure_assets("example-adventure")
    manager = TheaterManager(tmp_path)
    manager.create_theater("Checkers", "checkers", reference_files=references, playlists_data=playlists, lore_files=lore, theater_config=config)
    state = CanvasStateManager(manager.theater("checkers"))
    state.game.act(GameAction(name="move", arguments={"source": 40, "target": 33}, revision=0))
    restored = CanvasStateManager(manager.theater("checkers"))
    assert restored.game.state["board"][33] == 1
    assert restored.game.revision == 1


def test_zip_game_declaration_is_mounted_and_editable(tmp_path: Path) -> None:
    content = Path("adventures/example_adventure/game/game.json").read_bytes()
    archive = BytesIO()
    with ZipFile(archive, "w") as package:
        package.writestr("checkers/game/game.json", content)
    references, playlists, lore, _ = extract_asset_package(archive.getvalue())
    manager = TheaterManager(tmp_path)
    manager.create_theater("Checkers", "checkers", reference_files=references)
    assert (tmp_path / "checkers/game/game.json").read_bytes() == content
    assert safe_asset_path(tmp_path, "game/game.json") == tmp_path / "game/game.json"
    assert upload_path("checkers/game/game.json", folder=True) == "game/game.json"
    validate_text("game/game.json", content.decode("utf-8"))


def set_position(game: GameState, pieces: dict[int, int], quiet: int = 0) -> None:
    assert game.program is not None
    state = {**game.program.definition.initial, "board": [pieces.get(i, 0) for i in range(64)], "quiet": quiet}
    game.load({"definition_hash": game.definition_hash, "state": state, "revision": 0})


def move(game: GameState, source: int, target: int) -> None:
    game.act(GameAction(name="move", arguments={"source": source, "target": target}, revision=game.revision))


def test_package_cel_enforces_captures_and_continuation() -> None:
    game = GameState(Path("adventures/example_adventure"), Mock(), Mock())
    assert game.error == ""
    set_position(game, {42: 1, 46: 1, 35: -1, 21: -1, 1: -1})
    with pytest.raises(ValueError):
        move(game, 46, 37)
    move(game, 42, 28)
    assert game.state["turn"] == 1
    assert game.state["forced"] == 28
    assert game.state["board"][35] == 0
    with pytest.raises(ValueError):
        move(game, 46, 37)
    move(game, 28, 14)
    assert game.state["board"][21] == 0
    assert game.state["forced"] == -1
    assert game.state["turn"] == -1


def test_package_cel_crowns_and_ends_turn_then_kings_move_backwards() -> None:
    game = GameState(Path("adventures/example_adventure"), Mock(), Mock())
    set_position(game, {17: 1, 10: -1, 12: -1})
    move(game, 17, 3)
    assert game.state["board"][3] == 2
    assert game.state["turn"] == -1
    assert game.state["forced"] == -1
    set_position(game, {26: 2, 1: -1})
    move(game, 26, 35)
    assert game.state["board"][35] == 2


def test_package_cel_declares_wins_draws_and_reset() -> None:
    game = GameState(Path("adventures/example_adventure"), Mock(), Mock())
    set_position(game, {42: 1, 35: -1})
    move(game, 42, 28)
    assert game.state["winner"] == 1
    with pytest.raises(ValueError):
        move(game, 28, 19)
    game.act(GameAction(name="reset", revision=game.revision))
    assert game.state["board"].count(1) == 12
    assert game.revision == 2
    set_position(game, {17: 1, 62: -1})
    move(game, 17, 8)
    assert game.state["winner"] == 1
    set_position(game, {42: 2, 1: -2}, quiet=79)
    move(game, 42, 33)
    assert game.state["draw"] is True
