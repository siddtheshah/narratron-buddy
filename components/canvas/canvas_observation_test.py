"""The observation adapts real persisted objects without losing their identity."""
import io
import json
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
import pytest

from components.canvas.canvas_observation import canonicalize_actions, observe_page
from components.canvas.canvas_state_manager import CanvasStateManager
from components.canvas.doodle_state import DoodleAction, doodle_snapshot_batches
from components.theater_manager import TheaterManager
from game_engine.canvas_contract import Page


def page() -> Page:
    return Page(id="page-1", scene_id="door", asset_id="image:one", media_kind="image", animation=None, width=2000, height=1000)


def stamp_bytes(action: DoodleAction) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (100, 200), "red").save(output, format="PNG")
    return output.getvalue()


def test_migration_preserves_placements_and_assigns_durable_stroke_identity() -> None:
    actions: list[DoodleAction] = [
        {"type": "stamp", "id": "piece", "stamp_id": "theater:stage:key.png", "x": 0.0, "y": 0.5, "size": 80, "rotation": 90},
        {"type": "draw", "x0": 0.1, "y0": 0.2, "x1": 0.3, "y1": 0.4, "size": 3},
        {"type": "draw", "x0": 0.3, "y0": 0.4, "x1": 0.5, "y1": 0.6, "size": 3},
        {"type": "text", "id": "clue", "x": 0.1, "y": 0.2, "text": "Hello", "size": 32},
    ]
    migrated = canonicalize_actions(actions, page(), stamp_bytes)
    assert "canvas_width" not in actions[0]
    assert migrated == canonicalize_actions(migrated, page(), stamp_bytes)
    snapshot = observe_page(page(), migrated, revision=7)
    assert snapshot.revision == 7 and snapshot.event is None
    assert snapshot.stamps["piece"].asset_id == "stamp:key"
    assert snapshot.stamps["piece"].center.x == 0
    assert snapshot.stamps["piece"].bounds.model_dump() == {"x": -400, "y": 4600, "width": 800, "height": 800}
    assert snapshot.annotations["clue"].bounds.width == 1200
    assert len(snapshot.drawings) == 1
    stroke = next(iter(snapshot.drawings.values()))
    assert [point.model_dump() for point in stroke.points] == [{"x": 1000, "y": 2000}, {"x": 3000, "y": 4000}, {"x": 5000, "y": 6000}]
    restored = json.loads(json.dumps(migrated))
    assert observe_page(page(), restored, revision=7) == snapshot


def test_batches_do_not_merge_different_strokes_that_touch() -> None:
    actions: list[DoodleAction] = [
        {"type": "draw", "stroke_id": "one", "x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.5, "color": "red", "size": 3},
        {"type": "draw", "stroke_id": "two", "x0": 0.5, "y0": 0.5, "x1": 0.9, "y1": 0.9, "color": "red", "size": 3},
    ]
    assert [batch["stroke_id"] for batch in doodle_snapshot_batches(actions)] == ["one", "two"]
    assert len(observe_page(page(), canonicalize_actions(actions, page(), stamp_bytes), revision=0).drawings) == 2


def test_unavailable_stamp_media_is_an_explicit_error() -> None:
    def missing(action: DoodleAction) -> None:
        return None

    with pytest.raises(ValueError, match="media is unavailable"):
        canonicalize_actions([{"type": "stamp", "id": "s", "x": 0.5, "y": 0.5}], page(), missing)


def test_overlong_strokes_are_rejected_without_truncating_evidence() -> None:
    actions: list[DoodleAction] = [
        {"type": "draw", "stroke_id": "long", "x0": i / 300, "y0": 0.5, "x1": (i + 1) / 300, "y1": 0.5, "size": 3}
        for i in range(201)
    ]
    with pytest.raises(ValueError, match="200"):
        observe_page(page(), canonicalize_actions(actions, page(), stamp_bytes), revision=0)
    assert len(actions) == 201


def test_deleting_a_middle_segment_creates_no_invented_connecting_line() -> None:
    actions: list[DoodleAction] = [
        {"type": "draw", "id": str(i), "stroke_id": "stroke", "x0": i / 10, "y0": 0.5, "x1": (i + 1) / 10, "y1": 0.5, "size": 3}
        for i in range(4)
    ]
    migrated = canonicalize_actions(actions, page(), stamp_bytes)
    del migrated[1]
    split = canonicalize_actions(migrated, page(), stamp_bytes)
    snapshot = observe_page(page(), split, revision=1)
    assert len(snapshot.drawings) == 2
    assert [len(stroke.points) for stroke in snapshot.drawings.values()] == [2, 3]
    assert split == canonicalize_actions(split, page(), stamp_bytes)
    survivor = canonicalize_actions(split[1:], page(), stamp_bytes)
    assert survivor[0]["canvas_stroke_id"] == split[1]["canvas_stroke_id"]


def test_manager_observes_real_pages_and_persists_revision_and_geometry(tmp_path: Path) -> None:
    theater = TheaterManager(base_theaters_dir=tmp_path).theater("stage")
    directory = theater.scenes_dir() / "door"
    directory.mkdir(parents=True)
    image = directory / "1.png"
    Image.new("RGB", (2000, 1000), "black").save(image)
    state = CanvasStateManager(theater)
    state.visual.show_image(str(image))
    state.doodles.save_text({"type": "text", "id": "clue", "x": 0.1, "y": 0.2, "text": "Hello", "size": 32, "font": "Outfit"})
    first = state.canvas_observation()
    assert first.page is not None and first.page.scene_id == "door"
    assert "clue" in first.annotations
    assert state.doodles.doodles[0]["canvas_width"] == 1200
    assert state.canvas_observation() == first
    reloaded = CanvasStateManager(theater)
    assert reloaded.canvas_observation() == first
    reloaded.doodles.remove_text("clue")
    second = reloaded.canvas_observation()
    assert second.revision > first.revision and second.annotations == {}
    assert second.page == first.page


def test_invalid_canvas_revision_cannot_interrupt_story_hydration(tmp_path: Path) -> None:
    theater = TheaterManager(base_theaters_dir=tmp_path).theater("stage")
    theater.directory().mkdir(parents=True, exist_ok=True)
    (theater.directory() / "theater.json").write_text(json.dumps({"canvas_state": {
        "canvas_api_state": {"revision": "invalid", "fingerprint": "old"},
        "narration": "Keep the story.", "chat_messages": [{"author": "player", "text": "Keep chat."}],
    }}), encoding="utf-8")
    state = CanvasStateManager(theater)
    assert state.story.narration == "Keep the story."
    assert state.chat.get_messages() == [{"author": "player", "text": "Keep chat."}]
    with pytest.raises(ValueError, match="observation revision is invalid"):
        state.canvas_observation()
    assert state.serialized_state()["canvas_api_state"] == {"revision": "invalid", "fingerprint": "old"}


def test_failed_revision_save_retains_the_previous_clock(tmp_path: Path) -> None:
    theater = TheaterManager(base_theaters_dir=tmp_path).theater("stage")
    state = CanvasStateManager(theater)
    before = state.canvas_clock.model_copy(deep=True)
    with patch("utils.atomic_file.os.replace", side_effect=OSError("failed commit")):
        with pytest.raises(OSError, match="failed commit"):
            state.canvas_observation()
    assert state.canvas_clock == before
    assert state.canvas_observation().revision > before.revision


def test_storage_notification_failure_cannot_undo_a_durable_save(tmp_path: Path) -> None:
    changed = Mock()
    theater = TheaterManager(base_theaters_dir=tmp_path, storage_changed=changed).theater("stage")
    state = CanvasStateManager(theater)
    changed.side_effect = OSError("mirror unavailable")
    state.story.narration = "Saved locally."
    observed = state.canvas_observation()
    saved = json.loads((theater.directory() / "theater.json").read_text(encoding="utf-8"))
    assert saved["canvas_state"]["narration"] == "Saved locally."
    assert saved["canvas_state"]["canvas_api_state"]["revision"] == observed.revision
    assert state._storage_notification_pending is True
    changed.side_effect = None
    assert state.retry_storage_notification() is True
    assert state._storage_notification_pending is False
