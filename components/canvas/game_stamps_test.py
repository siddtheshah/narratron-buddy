"""Panel output stages native stamps and commits together with game state."""
import base64
import io
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
import pytest

from components.canvas.canvas_assets import CanvasAssetCatalog
from components.canvas.canvas_state_manager import CanvasStateManager
from components.canvas.game_stamps import stage_stamps
from components.theater_manager import TheaterManager
from game_engine.canvas_contract import CanvasAPI
from game_engine.game_runtime import GameAction, ViewNode
from game_engine.game_state import UnauthorizedGameAction
from game_engine.go_cel import find_go_worker_executable
from testing.game_fixtures import counter_definition


@pytest.fixture
def canvas(tmp_path: Path) -> CanvasStateManager:
    theater = TheaterManager(tmp_path).theater("stage")
    definition = counter_definition()
    definition.canvas_api = CanvasAPI(version=1, effects=["put_stamp", "remove_stamp"])
    definition.view = ViewNode(kind="text", props={"text": "string(size(canvas.stamps))"})
    definition.transitions["add"].canvas_effects = '[{"op":"put_stamp","target":{"page_id":canvas.page.id},"key":"reward","asset_id":"stamp:reward","center":{"x":5000,"y":5000},"width":1000,"rotation":0,"draggable":false}]'
    game_dir = theater.directory() / "game"
    game_dir.mkdir(parents=True, exist_ok=True)
    (game_dir / "game.json").write_text(definition.model_dump_json(), encoding="utf-8")
    stamps = theater.directory() / "stamps"
    stamps.mkdir()
    Image.new("RGBA", (200, 100), "gold").save(stamps / "reward.png")
    image = theater.directory() / "page.png"
    Image.new("RGB", (2000, 1000), "black").save(image)
    state = CanvasStateManager(theater)
    state.visual.show_image(str(image))
    return state


def add(canvas: CanvasStateManager) -> None:
    assert canvas.game is not None
    canvas.game.act(GameAction(name="add", arguments={"amount": 1}, revision=canvas.game.revision))


def test_stable_keys_update_in_place_and_view_reads_staged_output(canvas: CanvasStateManager) -> None:
    add(canvas)
    first = canvas.canvas_observation()
    first_stamp, = first.stamps.values()
    assert first_stamp.bounds.width == first_stamp.bounds.height == 1000
    assert canvas.game.payload()["view"][0]["props"]["text"] == "1"
    add(canvas)
    assert list(canvas.canvas_observation().stamps) == [first_stamp.id]
    restored = CanvasStateManager(canvas.theater)
    assert restored.game.state == {"count": 2}
    assert restored.canvas_observation() == first


def test_unknown_stamp_rejects_both_outputs(canvas: CanvasStateManager) -> None:
    (canvas.theater.directory() / "stamps" / "reward.png").unlink()
    with pytest.raises(ValueError, match="Unknown or ambiguous"):
        add(canvas)
    assert canvas.game.state == {"count": 0} and canvas.game.revision == 0
    assert canvas.canvas_observation().stamps == {}


def test_pinned_canvas_rejects_output_and_retains_position(canvas: CanvasStateManager) -> None:
    canvas.visual.set_pinned(True)
    with pytest.raises(ValueError, match="Unpin"):
        add(canvas)
    assert canvas.game.state == {"count": 0}
    assert canvas.canvas_observation().stamps == {}


def test_contributor_cannot_use_canvas_output_to_bypass_action_permissions(canvas: CanvasStateManager) -> None:
    with pytest.raises(UnauthorizedGameAction):
        canvas.game.act(GameAction(name="add", arguments={"amount": 1}, revision=0), actor="contributor")
    assert canvas.game.state == {"count": 0} and canvas.canvas_observation().stamps == {}


def test_postcommit_notification_failure_leaves_both_outputs_durable(canvas: CanvasStateManager) -> None:
    canvas.game._notify = Mock(side_effect=OSError("viewer unavailable"))
    add(canvas)
    restored = CanvasStateManager(canvas.theater)
    assert restored.game.state == {"count": 1}
    assert len(restored.canvas_observation().stamps) == 1


def test_unsupported_observation_is_a_game_error_without_hiding_story(canvas: CanvasStateManager) -> None:
    canvas.visual.shown_images_history[-1]["animation"] = {"format": "video"}
    canvas.story.narration = "Still available"
    payload = canvas.get_latest_state()
    assert payload["narration"] == "Still available"
    assert "animation catalog adapter" in payload["game_state"]["error"]


def test_staging_rejects_other_package_ownership(canvas: CanvasStateManager) -> None:
    add(canvas)
    snapshot = canvas.canvas_observation()
    stamp, = snapshot.stamps.values()
    catalog = CanvasAssetCatalog(canvas.theater.directory(), canvas.theater.get_url_for_path)
    with pytest.raises(ValueError, match="another package"):
        stage_stamps([{"op": "remove_stamp", "target": {"page_id": snapshot.page.id}, "key": stamp.game_key}], snapshot, canvas.doodles.doodles, catalog, "another-package")


def test_output_that_exceeds_observation_limits_is_rejected(canvas: CanvasStateManager) -> None:
    canvas.doodles.doodles = [{
        "type": "stamp", "id": str(i), "canvas_asset_id": "stamp:reward", "canvas_width": 500,
        "canvas_height": 500, "canvas_size": 50, "size": 50, "x": 0.1, "y": 0.1,
    } for i in range(256)]
    assert len(canvas.canvas_observation().stamps) == 256
    with pytest.raises(ValueError, match="256"):
        add(canvas)
    assert canvas.game.state == {"count": 0}
    assert len(canvas.canvas_observation().stamps) == 256


def test_fixed_game_stamps_survive_user_clear_and_cannot_be_moved_or_removed(canvas: CanvasStateManager) -> None:
    add(canvas)
    stamp, = canvas.doodles.stamp_annotations()
    before = canvas.canvas_observation()
    assert canvas.doodles.save_stamp({**stamp, "x": 0.1}) is False
    assert canvas.doodles.select_stamp(str(stamp["id"])) is False
    assert canvas.doodles.remove_stamp(str(stamp["id"])) is False
    canvas.doodles.add({"type": "clear"})
    assert canvas.canvas_observation() == before


def test_game_stamps_placed_on_same_doodle_layer_as_regular_stamps(canvas: CanvasStateManager) -> None:
    buf = io.BytesIO()
    Image.new("RGBA", (100, 100), "red").save(buf, format="PNG")
    data_url = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"

    draw_stroke: dict[str, str | float | int | None] = {
        "type": "draw", "stroke_id": "stroke-1", "x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.5, "color": "#ff0000", "size": 3,
    }
    text_note: dict[str, str | float | int | None] = {
        "type": "text", "id": "text-1", "x": 0.4, "y": 0.4, "text": "Note", "color": "#ffffff", "size": 24,
    }
    user_stamp_1: dict[str, str | float | int | None] = {
        "type": "stamp", "id": "user-stamp-1", "stamp_id": 10, "user_id": 1,
        "url": data_url, "name": "User Stamp 1", "x": 0.2, "y": 0.2, "size": 80.0,
    }
    canvas.doodles.add([draw_stroke])
    canvas.doodles.save_text(text_note)
    assert canvas.doodles.save_stamp(user_stamp_1) is True

    assert [d.get("type") for d in canvas.doodles.doodles] == ["stamp", "draw", "text"]
    assert canvas.doodles.doodles[0]["id"] == "user-stamp-1"

    add(canvas)

    types_after_game_stamp = [d.get("type") for d in canvas.doodles.doodles]
    assert types_after_game_stamp == ["stamp", "stamp", "draw", "text"]
    stamp_ids = [str(d.get("id")) for d in canvas.doodles.doodles if d.get("type") == "stamp"]
    assert stamp_ids[0] == "user-stamp-1"
    game_stamp_id = stamp_ids[1]

    user_stamp_2: dict[str, str | float | int | None] = {
        "type": "stamp", "id": "user-stamp-2", "stamp_id": 11, "user_id": 1,
        "url": data_url, "name": "User Stamp 2", "x": 0.3, "y": 0.3, "size": 80.0,
    }
    assert canvas.doodles.save_stamp(user_stamp_2) is True
    types_after_user_stamp2 = [d.get("type") for d in canvas.doodles.doodles]
    assert types_after_user_stamp2 == ["stamp", "stamp", "stamp", "draw", "text"]
    stamp_ids_final = [str(d.get("id")) for d in canvas.doodles.doodles if d.get("type") == "stamp"]
    assert stamp_ids_final == ["user-stamp-1", game_stamp_id, "user-stamp-2"]

    obs = canvas.canvas_observation()
    assert set(obs.stamps.keys()) == {"user-stamp-1", game_stamp_id, "user-stamp-2"}
    assert obs.stamps[game_stamp_id].managed_by == "game"
    assert obs.stamps["user-stamp-1"].managed_by == "user"


def test_go_commits_native_stamps_and_reuses_the_same_placement(canvas: CanvasStateManager, monkeypatch: pytest.MonkeyPatch) -> None:
    if find_go_worker_executable() is None:
        pytest.skip("Build the CEL-Go worker to verify canvas commits with Go.")
    add(canvas)
    before = canvas.canvas_observation()
    monkeypatch.setenv("NARRATRON_CEL_BACKEND", "go")
    restored = CanvasStateManager(canvas.theater)
    assert restored.game.worker is not None
    add(restored)
    assert restored.game.state == {"count": 2}
    assert restored.canvas_observation() == before


def test_game_stamp_replaces_existing_user_stamp_under_one_at_a_time_rule(canvas: CanvasStateManager) -> None:
    buf = io.BytesIO()
    Image.new("RGBA", (100, 100), "gold").save(buf, format="PNG")
    data_url = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"

    theater_stamp_action: dict[str, str | float | int | bool | None] = {
        "type": "stamp",
        "id": "pre-existing-user-reward",
        "stamp_id": "theater:stage:reward.png",
        "user_id": 1,
        "url": data_url,
        "name": "reward",
        "x": 0.25,
        "y": 0.25,
        "size": 80.0,
    }
    assert canvas.doodles.save_stamp(theater_stamp_action) is True
    assert len(canvas.doodles.stamp_annotations()) == 1
    assert canvas.doodles.stamp_annotations()[0]["id"] == "pre-existing-user-reward"

    add(canvas)

    stamps = canvas.doodles.stamp_annotations()
    assert len(stamps) == 1
    assert stamps[0]["managed_by"] == "game"
    assert stamps[0]["canvas_asset_id"] == "stamp:reward"
    assert stamps[0]["name"] == "reward"


def test_user_stamp_replaces_existing_game_stamp_under_one_at_a_time_rule(canvas: CanvasStateManager) -> None:
    catalog = CanvasAssetCatalog(canvas.theater.directory(), canvas.theater.get_url_for_path)
    snapshot = canvas.canvas_observation()
    effects = [{
        "op": "put_stamp",
        "target": {"page_id": snapshot.page.id},
        "key": "reward",
        "asset_id": "stamp:reward",
        "center": {"x": 5000, "y": 5000},
        "width": 1000,
        "rotation": 0,
        "draggable": True,
    }]
    staged, observed = stage_stamps(effects, snapshot, canvas.doodles.doodles, catalog, "test-pkg")
    canvas.doodles.doodles = staged
    stamps = canvas.doodles.stamp_annotations()
    assert len(stamps) == 1
    assert stamps[0]["managed_by"] == "game"

    buf = io.BytesIO()
    Image.new("RGBA", (100, 100), "gold").save(buf, format="PNG")
    data_url = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"

    user_replacement: dict[str, str | float | int | bool | None] = {
        "type": "stamp",
        "id": "user-replacement-reward",
        "stamp_id": "theater:stage:reward.png",
        "user_id": 1,
        "url": data_url,
        "name": "reward",
        "x": 0.6,
        "y": 0.6,
        "size": 80.0,
    }
    assert canvas.doodles.save_stamp(user_replacement) is True
    stamps_after = canvas.doodles.stamp_annotations()
    assert len(stamps_after) == 1
    assert stamps_after[0]["id"] == "user-replacement-reward"
    assert stamps_after[0]["user_id"] == 1


def test_draggable_game_stamp_is_selectable_and_movable(canvas: CanvasStateManager) -> None:
    catalog = CanvasAssetCatalog(canvas.theater.directory(), canvas.theater.get_url_for_path)
    snapshot = canvas.canvas_observation()
    effects = [{
        "op": "put_stamp",
        "target": {"page_id": snapshot.page.id},
        "key": "draggable-reward",
        "asset_id": "stamp:reward",
        "center": {"x": 5000, "y": 5000},
        "width": 1000,
        "rotation": 0,
        "draggable": True,
    }]
    staged, observed = stage_stamps(effects, snapshot, canvas.doodles.doodles, catalog, "test-pkg")
    canvas.doodles.doodles = staged
    stamps = canvas.doodles.stamp_annotations()
    assert len(stamps) == 1
    assert stamps[0]["draggable"] is True
    stamp_id = str(stamps[0]["id"])
    assert canvas.doodles.select_stamp(stamp_id) is True
    assert canvas.doodles.save_stamp({**stamps[0], "x": 0.8, "y": 0.8}) is True
    updated = canvas.doodles.stamp_annotations()
    assert len(updated) == 1
    assert updated[0]["x"] == 0.8 and updated[0]["y"] == 0.8

