"""Asset catalog and shared playback route coverage."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from api_server import theaters
from components.theater_manager import TheaterManager
from components.canvas.audio_state import AudioState
from services.theater_assets import animations, playlists


@pytest.mark.asyncio
async def test_no_music_clears_shared_playback_even_when_pinned() -> None:
    request = Request({"type": "http", "headers": []})
    notify = MagicMock()
    state = MagicMock()
    state.audio = AudioState(notify)
    state.audio.update_music("Battle", ["/battle.mp3"])
    state.audio.set_pinned(True)
    with patch.object(theaters, "_require_canvas_access_async", AsyncMock()), \
         patch.object(theaters, "get_current_user_async", AsyncMock(return_value={"id": 1})), \
         patch.object(theaters, "can_control_agent_websocket", return_value=True), \
         patch.object(theaters.db, "get_deployment", return_value={}), \
         patch.object(theaters.canvas_states, "get", return_value=state):
        response = await theaters.push_theater_track(theaters.PushAssetRequest(id="no_music"), request, "asset-stage")
    assert response["message"] == "Music stopped."
    assert state.audio.payload()["music_id"] == ""
    assert state.audio.payload()["tracks"] == []
    assert state.audio.payload()["time"] > 0
    notify.assert_called_with("latest")
    state.persist.assert_called_once()


@pytest.mark.asyncio
async def test_asset_catalog_and_individual_track_push(tmp_path: Path) -> None:
    manager = TheaterManager(base_theaters_dir=tmp_path)
    theater = manager.theater("asset-stage")
    track = theater.playlists_dir() / "Quiet evening" / "nested" / "A song.wav"
    track.parent.mkdir(parents=True)
    track.write_bytes(b"audio")
    generated = theater.music_artifacts_dir() / "generated.mp3"
    generated.parent.mkdir(parents=True)
    generated.write_bytes(b"audio")
    groups = playlists(theater)
    assert [group.name for group in groups] == ["Quiet evening", "Generated music"]
    assert groups[0].tracks[0].url == "/theaters/asset-stage/playlists/Quiet%20evening/nested/A%20song.wav"
    assert groups[1].tracks[0].url.endswith("/output/music/generated.mp3")
    request = Request({"type": "http", "headers": []})
    state = MagicMock()
    with patch.object(theaters, "theater_manager", manager), \
         patch.object(theaters, "_require_canvas_access_async", AsyncMock()), \
         patch.object(theaters, "get_current_user_async", AsyncMock(return_value={"id": 1})), \
         patch.object(theaters, "can_control_agent_websocket", return_value=True), \
         patch.object(theaters.db, "get_deployment", return_value={}), \
         patch.object(theaters.canvas_states, "get", return_value=state):
        response = await theaters.push_theater_track(theaters.PushAssetRequest(id=groups[0].tracks[0].id), request, "asset-stage")
        assert response["status"] == "ok"
        state.audio.update_music.assert_called_once_with("Quiet evening / A song", [groups[0].tracks[0].url], allow_pinned=True)
        state.persist.assert_called_once()
        with pytest.raises(HTTPException) as missing:
            await theaters.push_theater_track(theaters.PushAssetRequest(id="../../outside.mp3"), request, "asset-stage")
        assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_animations_resolve_local_files_and_push(tmp_path: Path) -> None:
    manager = TheaterManager(base_theaters_dir=tmp_path)
    theater = manager.theater("asset-stage")
    root = theater.output_dir() / "animations"
    video = root / "movie"
    video.mkdir(parents=True)
    (video / "video.mp4").write_bytes(b"video")
    (video / "video.json").write_text(json.dumps({"scene_prompt": "Storm", "video_path": "/old/deployment/video.mp4"}))
    frames = root / "frames"
    frames.mkdir()
    for n in range(1, 4):
        (frames / f"frame_{n}.jpg").write_bytes(b"frame")
    broken = root / "broken"
    broken.mkdir()
    (broken / "video.json").write_text("invalid")
    layered = root / "layered"
    layered.mkdir()
    for filename in ("base.png", "foreground.png"):
        (layered / filename).write_bytes(b"image")
    (layered / "layered.json").write_text(json.dumps({"layers": [
        {"path": "base.png", "effect": "none"},
        {"path": "foreground.png", "effect": "gentle_rocking"},
    ]}))
    assets = animations(theater)
    assert [asset.id for asset in assets] == ["frames", "layered", "movie"]
    assert assets[2].manifest["video_path"] == str((video / "video.mp4").resolve())
    assert assets[1].manifest["base_image"] == str((layered / "base.png").resolve())
    assert len(assets[0].frames) == 3
    request = Request({"type": "http", "headers": []})
    state = MagicMock()
    state.visual.update_animation.return_value = {"status": "displayed", "message": "Playing Storm"}
    with patch.object(theaters, "theater_manager", manager), \
         patch.object(theaters, "_require_canvas_access_async", AsyncMock()), \
         patch.object(theaters, "get_current_user_async", AsyncMock(return_value={"id": 1})), \
         patch.object(theaters, "can_control_agent_websocket", return_value=True), \
         patch.object(theaters.db, "get_deployment", return_value={}), \
         patch.object(theaters.canvas_states, "get", return_value=state):
        listed = await theaters.list_theater_animations(request, "asset-stage")
        assert listed[2].manifest == {}
        assert listed[2].description == "Storm"
        response = await theaters.push_theater_animation(theaters.PushAssetRequest(id="movie"), request, "asset-stage")
        assert response["status"] == "ok"
        assert state.visual.update_animation.call_args.args[0] == "video"
        state.persist.assert_called_once()


@pytest.mark.asyncio
async def test_new_asset_routes_reject_viewers() -> None:
    request = Request({"type": "http", "headers": []})
    with patch.object(theaters, "_require_canvas_access_async", AsyncMock()), \
         patch.object(theaters, "get_current_user_async", AsyncMock(return_value={"id": 2})), \
         patch.object(theaters, "can_control_agent_websocket", return_value=False), \
         patch.object(theaters.db, "get_deployment", return_value={}):
        for route in (theaters.list_theater_playlists, theaters.list_theater_animations):
            with pytest.raises(HTTPException) as denied:
                await route(request, "asset-stage")
            assert denied.value.status_code == 403
        for push in (theaters.push_theater_track, theaters.push_theater_animation):
            with pytest.raises(HTTPException) as denied:
                await push(theaters.PushAssetRequest(id="track"), request, "asset-stage")
            assert denied.value.status_code == 403
