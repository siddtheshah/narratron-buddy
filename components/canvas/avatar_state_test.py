"""Tests for AvatarState manifest loading and canvas_state integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from components.canvas.avatar_state import AvatarState
from components.canvas.canvas_state_manager import CanvasStateManager
from components.theater_manager import Theater, TheaterManager


def _write_avatar_theater(
    root: Path,
    theater_id: str,
    *,
    avatar_config: str,
    package: str | None = None,
    manifest: dict[str, str | list[dict[str, str | bool]]] | None = None,
) -> Theater:
    theater_dir = root / theater_id
    theater_dir.mkdir(parents=True, exist_ok=True)
    if avatar_config:
        (theater_dir / "theater.yaml").write_text(avatar_config, encoding="utf-8")
    if package is not None and manifest is not None:
        avatar_dir = theater_dir / "avatars" / package
        avatar_dir.mkdir(parents=True, exist_ok=True)
        (avatar_dir / "avatar.json").write_text(json.dumps(manifest), encoding="utf-8")
    manager = TheaterManager(str(root))
    return manager.theater(theater_id)


def test_avatar_state_disabled_by_default_ignores_set_action(tmp_path: Path) -> None:
    theater = _write_avatar_theater(tmp_path, "disabled_stage", avatar_config="")
    notified: list[str] = []
    state = AvatarState(theater, notified.append)

    assert state.enabled is False
    assert state.actions == ()
    assert state.set_action("greeting") is False
    assert notified == []
    assert state.payload() == {
        "enabled": False,
        "package": "",
        "action": None,
        "sequence": 0,
        "time": 0.0,
    }


def test_avatar_state_omitted_avatar_config_ignores_existing_narratron_manifest(tmp_path: Path) -> None:
    theater = _write_avatar_theater(
        tmp_path,
        "adventure_without_avatar_config",
        avatar_config="story_planning:\n  adventure_mode: true\n",
        package="narratron",
        manifest={
            "id": "narratron",
            "title": "Narratron",
            "idle": "idle",
            "actions": [
                {"id": "idle", "label": "Idle", "loop": True},
            ],
        },
    )
    notified: list[str] = []
    state = AvatarState(theater, notified.append)

    assert state.enabled is False
    assert state.package == ""
    assert state.actions == ()
    assert notified == []


def test_avatar_state_disabled_in_config_ignores_manifest(tmp_path: Path) -> None:
    theater = _write_avatar_theater(
        tmp_path,
        "explicit_disabled",
        avatar_config="avatar:\n  enabled: false\n  package: narratron\n",
        package="narratron",
        manifest={
            "id": "narratron",
            "title": "Narratron",
            "idle": "idle",
            "actions": [
                {"id": "idle", "label": "Idle", "loop": True},
                {"id": "greeting", "label": "Greeting", "loop": False},
            ],
        },
    )
    notified: list[str] = []
    state = AvatarState(theater, notified.append)

    assert state.enabled is False
    assert state.actions == ()
    assert state.set_action("greeting") is False
    assert notified == []


def test_avatar_state_loads_manifest_and_tracks_actions(tmp_path: Path) -> None:
    theater = _write_avatar_theater(
        tmp_path,
        "narratron_stage",
        avatar_config="avatar:\n  enabled: true\n  package: narratron\n",
        package="narratron",
        manifest={
            "id": "narratron",
            "title": "Narratron",
            "idle": "idle",
            "actions": [
                {
                    "id": "idle",
                    "label": "Idle",
                    "clip": "Idle",
                    "loop": True,
                    "lightColor": "#7ce8ff",
                    "description": "Serene cosmic float.",
                },
                {
                    "id": "greeting",
                    "label": "Greeting",
                    "clip": "Greeting",
                    "loop": False,
                    "lightColor": "#ffd27d",
                    "description": "Welcoming bow and wave.",
                },
                {
                    "id": "greeting",
                    "label": "Duplicate",
                    "clip": "Greeting",
                    "loop": False,
                },
            ],
        },
    )
    notified: list[str] = []
    state = AvatarState(theater, notified.append)

    assert state.enabled is True
    assert state.package == "narratron"
    assert state.title == "Narratron"
    assert state.idle_action == "idle"
    assert [action.id for action in state.actions] == ["idle", "greeting"]
    assert state.actions[1].light_color == "#ffd27d"
    assert state.actions[1].description == "Welcoming bow and wave."

    assert state.set_action("greeting") is True
    assert state.current_action == "greeting"
    assert state.action_sequence == 1
    assert state.action_time > 0.0
    assert notified == ["latest"]

    assert state.set_action("greeting") is True
    assert state.action_sequence == 2
    assert notified == ["latest", "latest"]

    payload = state.payload()
    assert payload == {
        "enabled": True,
        "package": "narratron",
        "action": "greeting",
        "sequence": 2,
        "time": state.action_time,
    }


def test_avatar_state_rejects_unknown_action_when_enabled(tmp_path: Path) -> None:
    theater = _write_avatar_theater(
        tmp_path,
        "strict_stage",
        avatar_config="avatar:\n  enabled: true\n  package: narratron\n",
        package="narratron",
        manifest={
            "id": "narratron",
            "title": "Narratron",
            "idle": "idle",
            "actions": [
                {"id": "idle", "label": "Idle", "loop": True},
                {"id": "amused", "label": "Amused", "loop": False},
            ],
        },
    )
    state = AvatarState(theater, lambda *_: None)

    with pytest.raises(ValueError, match="Unknown avatar action 'backflip'"):
        state.set_action("backflip")


def test_avatar_state_missing_manifest_remains_disabled(tmp_path: Path) -> None:
    theater = _write_avatar_theater(
        tmp_path,
        "missing_manifest",
        avatar_config="avatar:\n  enabled: true\n  package: ghost\n",
    )
    state = AvatarState(theater, lambda *_: None)
    assert state.enabled is False
    assert state.set_action("idle") is False


def test_canvas_state_manager_exposes_avatar_payload_and_resets(tmp_path: Path) -> None:
    theater = _write_avatar_theater(
        tmp_path,
        "canvas_avatar_stage",
        avatar_config="avatar:\n  enabled: true\n  package: light-cube\n",
        package="light-cube",
        manifest={
            "id": "light-cube",
            "title": "Cube of Light",
            "idle": "idle",
            "actions": [
                {"id": "idle", "label": "Gold", "loop": True, "lightColor": "#ffc477"},
                {"id": "ember", "label": "Ember", "lightColor": "#ff5940"},
            ],
        },
    )
    canvas = CanvasStateManager(theater)
    assert canvas.avatar is not None
    assert canvas.avatar.enabled is True
    assert canvas.get_latest_state()["avatar"] == {
        "enabled": True,
        "package": "light-cube",
        "action": None,
        "sequence": 0,
        "time": 0.0,
    }

    assert canvas.avatar.set_action("ember") is True
    latest_avatar = canvas.get_latest_state()["avatar"]
    assert latest_avatar == {
        "enabled": True,
        "package": "light-cube",
        "action": "ember",
        "sequence": 1,
        "time": canvas.avatar.action_time,
    }

    canvas.reset()
    assert canvas.get_latest_state()["avatar"] == {
        "enabled": True,
        "package": "light-cube",
        "action": None,
        "sequence": 0,
        "time": 0.0,
    }
