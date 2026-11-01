"""Canvas contract validation rejects ambiguous or unbounded evidence and writes."""
from copy import deepcopy

import pytest
from pydantic import JsonValue, ValidationError

from game_engine.canvas_contract import CanvasAPI, CanvasSnapshot, canonical_key, commands_json, validate_effects


def scene_snapshot() -> CanvasSnapshot:
    data = CanvasSnapshot.empty(42).model_dump(mode="json")
    data["page"] = {
        "id": "page-7", "scene_id": "door", "asset_id": "scene:door:1",
        "media_kind": "image", "animation": None, "width": 1536, "height": 1024,
    }
    data["regions"] = {"lock": {"id": "lock", "bounds": {"x": 6800, "y": 4200, "width": 1000, "height": 1600}}}
    return CanvasSnapshot.model_validate(data)


def stamp_write(target: dict[str, str] | None = None) -> dict[str, JsonValue]:
    return {
        "op": "put_stamp", "target": target or {"page_id": "page-7"}, "key": "key-on-peg",
        "asset_id": "stamp:brass-key", "center": {"x": 7200, "y": 4800},
        "width": 600, "rotation": 0, "draggable": True,
    }


def capabilities() -> CanvasAPI:
    return CanvasAPI(version=1, effects=["show_scene", "show_animation", "put_stamp", "remove_stamp", "set_stamp_tray", "set_overlay"])


def test_empty_observation_has_every_root_and_allows_a_tray() -> None:
    snapshot = CanvasSnapshot.empty()
    data = snapshot.model_dump(mode="json")
    assert set(data) == {"version", "revision", "page", "stamps", "drawings", "annotations", "regions", "tray", "overlay", "event"}
    data["tray"] = ["stamp:key"]
    assert CanvasSnapshot.model_validate(data).tray == ["stamp:key"]
    del data["event"]
    with pytest.raises(ValidationError):
        CanvasSnapshot.model_validate(data)


@pytest.mark.parametrize("value", [True, 1.0, "1", 2])
def test_version_is_an_exact_supported_integer(value: JsonValue) -> None:
    data = CanvasSnapshot.empty().model_dump(mode="json")
    data["version"] = value
    with pytest.raises(ValidationError):
        CanvasSnapshot.model_validate(data)
    with pytest.raises(ValidationError):
        CanvasAPI.model_validate({"version": value, "effects": []})


@pytest.mark.parametrize("field,value", [("revision", -1), ("revision", 9_007_199_254_740_992), ("revision", True)])
def test_snapshot_rejects_invalid_numbers(field: str, value: JsonValue) -> None:
    data = CanvasSnapshot.empty().model_dump(mode="json")
    data[field] = value
    with pytest.raises(ValidationError):
        CanvasSnapshot.model_validate(data)


@pytest.mark.parametrize("field", ["stamps", "drawings", "annotations", "regions"])
def test_page_collection_keys_must_match_ids(field: str) -> None:
    entries: dict[str, JsonValue] = {
        "stamps": {"id": "real", "asset_id": "stamp:key", "managed_by": "user", "game_key": None, "draggable": True, "center": {"x": 0, "y": 0}, "bounds": {"x": -100, "y": -100, "width": 200, "height": 200}, "rotation": 0},
        "drawings": {"id": "real", "points": [], "bounds": {"x": 0, "y": 0, "width": 0, "height": 0}},
        "annotations": {"id": "real", "text": "note", "bounds": {"x": 0, "y": 0, "width": 1, "height": 1}},
        "regions": {"id": "real", "bounds": {"x": 0, "y": 0, "width": 1, "height": 1}},
    }
    data = scene_snapshot().model_dump(mode="json")
    data[field] = {"wrong": entries[field]}
    with pytest.raises(ValueError, match="keys must match"):
        CanvasSnapshot.model_validate(data)


def test_missing_page_cannot_expose_page_content() -> None:
    data = scene_snapshot().model_dump(mode="json")
    data["page"] = None
    with pytest.raises(ValueError, match="without a page"):
        CanvasSnapshot.model_validate(data)


@pytest.mark.parametrize("kind,animation", [
    ("animation", None), ("video", None),
    ("image", {"id": "a", "format": "layered", "playback": "once"}),
    ("video", {"id": "a", "format": "triframe", "playback": "loop"}),
    ("animation", {"id": "a", "format": "video", "playback": "once"}),
])
def test_page_media_and_playback_are_consistent(kind: str, animation: JsonValue) -> None:
    data = scene_snapshot().model_dump(mode="json")
    page = scene_snapshot().page
    assert page is not None
    data["page"] = {**page.model_dump(mode="json"), "media_kind": kind, "animation": animation}
    with pytest.raises(ValidationError):
        CanvasSnapshot.model_validate(data)


@pytest.mark.parametrize("revision,page_id", [(41, "page-7"), (42, "page-8")])
def test_evidence_cannot_be_rebound_to_a_new_snapshot(revision: int, page_id: str) -> None:
    data = scene_snapshot().model_dump(mode="json")
    data["event"] = {
        "id": "input-1", "kind": "stamp_removed", "actor": "player", "page_id": page_id,
        "canvas_revision": revision, "object_ids": ["deleted"], "point": None,
        "region_ids": [], "key": None, "binding_id": None,
    }
    with pytest.raises(ValueError, match="observed page and revision"):
        CanvasSnapshot.model_validate(data)


def test_removed_evidence_can_reference_an_absent_object() -> None:
    data = scene_snapshot().model_dump(mode="json")
    data["event"] = {
        "id": "input-1", "kind": "stamp_removed", "actor": "contributor", "page_id": "page-7",
        "canvas_revision": 42, "object_ids": ["deleted"], "point": None,
        "region_ids": [], "key": None, "binding_id": None,
    }
    snapshot = CanvasSnapshot.model_validate(data)
    assert snapshot.event is not None and snapshot.event.object_ids == ["deleted"]
    assert snapshot.stamps == {}


def test_byte_limit_applies_across_individually_bounded_annotations() -> None:
    data = scene_snapshot().model_dump(mode="json")
    data["annotations"] = {
        str(i): {"id": str(i), "text": "界" * 2000, "bounds": {"x": 0, "y": 0, "width": 1, "height": 1}}
        for i in range(24)
    }
    with pytest.raises(ValueError, match="128 KiB"):
        CanvasSnapshot.model_validate(data)


def test_scene_plus_stamp_batch_roundtrips_without_mutating_observation() -> None:
    snapshot = scene_snapshot()
    before = snapshot.model_dump(mode="json")
    commands: list[JsonValue] = [
        {"op": "show_scene", "scene_id": "room", "page_ref": "arrival"},
        stamp_write({"page_ref": "arrival"}),
    ]
    assert commands_json(validate_effects(commands, capabilities(), snapshot)) == commands
    assert snapshot.model_dump(mode="json") == before


@pytest.mark.parametrize("commands", [
    [stamp_write({"page_ref": "later"}), {"op": "show_scene", "scene_id": "room", "page_ref": "later"}],
    [stamp_write({"page_id": "history-page"})],
    [stamp_write({"page_id": "page-7", "page_ref": "arrival"})],
    [stamp_write(), stamp_write()],
    [stamp_write(), {"op": "remove_stamp", "target": {"page_id": "page-7"}, "key": "key-on-peg"}],
    [{"op": "show_scene", "scene_id": "room", "page_ref": "a"}, {"op": "show_animation", "animation_id": "lift", "playback": "once", "page_ref": "b"}],
    [{"op": "set_stamp_tray", "asset_ids": []}, {"op": "set_stamp_tray", "asset_ids": []}],
    [{"op": "set_overlay", "target": {"page_id": "page-7"}, "items": [], "filters": []}] * 2,
])
def test_ambiguous_batches_are_rejected(commands: list[JsonValue]) -> None:
    with pytest.raises(ValueError):
        validate_effects(commands, capabilities(), scene_snapshot())


def test_removing_a_missing_key_is_a_valid_command() -> None:
    commands: list[JsonValue] = [{"op": "remove_stamp", "target": {"page_id": "page-7"}, "key": "absent"}]
    assert commands_json(validate_effects(commands, capabilities(), scene_snapshot())) == commands


def test_read_only_packages_cannot_emit_commands() -> None:
    with pytest.raises(ValueError, match="not declared"):
        validate_effects([stamp_write()], CanvasAPI(version=1, effects=[]), scene_snapshot())


@pytest.mark.parametrize("field,value", [
    ("width", 0), ("width", 1.5), ("rotation", 360_000), ("draggable", 1),
    ("center", {"x": -1, "y": 1}), ("center", {"x": 1, "y": 10_001}),
    ("url", "https://example.invalid/key.png"),
])
def test_stamp_writes_are_strict(field: str, value: JsonValue) -> None:
    command = stamp_write()
    command[field] = value
    with pytest.raises(ValueError):
        validate_effects([command], capabilities(), scene_snapshot())


def test_overlay_highlights_must_match_authored_geometry() -> None:
    command: dict[str, JsonValue] = {
        "op": "set_overlay", "target": {"page_id": "page-7"}, "filters": [],
        "items": [{"id": "highlight", "kind": "region", "bounds": {"x": 6800, "y": 4200, "width": 1000, "height": 1600}, "opacity": 10000, "region_id": "lock", "color": "#ffffff"}],
    }
    assert len(validate_effects([command], capabilities(), scene_snapshot())) == 1
    wrong = deepcopy(command)
    wrong["items"] = [{"id": "highlight", "kind": "region", "bounds": {"x": 0, "y": 0, "width": 1, "height": 1}, "opacity": 10000, "region_id": "lock", "color": "#ffffff"}]
    with pytest.raises(ValueError, match="existing region's bounds"):
        validate_effects([wrong], capabilities(), scene_snapshot())


@pytest.mark.parametrize("filters", [
    [{"kind": "dim", "amount": 1}, {"kind": "dim", "amount": 2}],
    [{"kind": "tint", "amount": 1}],
    [{"kind": "dim", "amount": 1, "color": "#000000"}],
    [{"kind": "blur", "amount": 10001}],
])
def test_overlay_filters_reject_ambiguous_or_unsupported_values(filters: list[JsonValue]) -> None:
    with pytest.raises(ValueError):
        validate_effects([{"op": "set_overlay", "target": {"page_id": "page-7"}, "items": [], "filters": filters}], capabilities(), scene_snapshot())


def test_output_byte_and_command_limits() -> None:
    with pytest.raises(ValueError, match="64"):
        validate_effects([{"op": "remove_stamp", "target": {"page_id": "page-7"}, "key": str(i)} for i in range(65)], capabilities(), scene_snapshot())
    items: list[JsonValue] = [
        {"id": str(i), "kind": "text", "bounds": {"x": 0, "y": 0, "width": 1, "height": 1}, "opacity": 10000, "text": "界" * 2000, "font_size": 100, "align": "left", "color": "#000000"}
        for i in range(3)
    ]
    with pytest.raises(ValueError, match="16 KiB"):
        validate_effects([{"op": "set_overlay", "target": {"page_id": "page-7"}, "items": items, "filters": []}], capabilities(), scene_snapshot())


@pytest.mark.parametrize("key,expected", [("ArrowUp", "ArrowUp"), (" ", " "), ("Z", "z"), ("1", "1")])
def test_shortcut_normalization(key: str, expected: str) -> None:
    assert canonical_key(key) == expected


@pytest.mark.parametrize("key", ["Escape", "Tab", "é", "!", "Space", "", "Control"])
def test_shortcut_keys_exclude_navigation_modifiers_and_non_ascii(key: str) -> None:
    with pytest.raises(ValueError):
        canonical_key(key)
