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
    mock_session = MagicMock()
    mock_session.is_alive = True
    with patch.object(theaters, "_require_canvas_access_async", AsyncMock()), \
         patch.object(theaters, "get_current_user_async", AsyncMock(return_value={"id": 1})), \
         patch.object(theaters, "can_control_agent_websocket", return_value=True), \
         patch.object(theaters.db, "get_deployment", return_value={}), \
         patch.object(theaters.live_agent_manager, "get_session", return_value=mock_session), \
         patch.object(theaters.canvas_states, "get", return_value=state):
        response = await theaters.push_theater_track(theaters.PushAssetRequest(id="no_music"), request, "asset-stage")
    assert response["message"] == "Music stopped."
    assert state.audio.payload()["music_id"] == ""
    assert state.audio.payload()["tracks"] == []
    assert state.audio.payload()["time"] > 0
    notify.assert_called_with("latest")
    state.persist.assert_called_once()
    mock_session.send_content.assert_called_once()
    call_content, = mock_session.send_content.call_args.args
    assert call_content.role == "system"
    assert call_content.parts[0].text == "[Orator Action] The active orator stopped the music."
    assert mock_session.send_content.call_args.kwargs.get("partial") is True


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
    assert [group.id for group in groups] == ["playlist:Quiet%20evening", "generated:music"]
    assert groups[0].tracks[0].url == "/theaters/asset-stage/playlists/Quiet%20evening/nested/A%20song.wav"
    assert groups[1].tracks[0].url.endswith("/output/music/generated.mp3")
    request = Request({"type": "http", "headers": []})
    state = MagicMock()
    mock_session = MagicMock()
    mock_session.is_alive = True
    with patch.object(theaters, "theater_manager", manager), \
         patch.object(theaters, "_require_canvas_access_async", AsyncMock()), \
         patch.object(theaters, "get_current_user_async", AsyncMock(return_value={"id": 1})), \
         patch.object(theaters, "can_control_agent_websocket", return_value=True), \
         patch.object(theaters.db, "get_deployment", return_value={}), \
         patch.object(theaters.live_agent_manager, "get_session", return_value=mock_session), \
         patch.object(theaters.canvas_states, "get", return_value=state):
        response = await theaters.push_theater_track(theaters.PushAssetRequest(id=groups[0].tracks[0].id), request, "asset-stage")
        assert response["status"] == "ok"
        state.audio.update_music.assert_called_once_with("Quiet evening / A song", [groups[0].tracks[0].url], allow_pinned=True)
        state.persist.assert_called_once()
        mock_session.send_content.assert_called_once()
        call_content, = mock_session.send_content.call_args.args
        assert call_content.role == "system"
        assert call_content.parts[0].text == f"[Orator Action] The active orator changed the music to '{groups[0].tracks[0].name}'."
        assert mock_session.send_content.call_args.kwargs.get("partial") is True
        with pytest.raises(HTTPException) as missing:
            await theaters.push_theater_track(theaters.PushAssetRequest(id="../../outside.mp3"), request, "asset-stage")
        assert missing.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("generated", [False, True])
async def test_entire_playlist_push_updates_shared_playback_when_pinned(tmp_path: Path, generated: bool) -> None:
    manager = TheaterManager(base_theaters_dir=tmp_path)
    theater = manager.theater("asset-stage")
    root = theater.music_artifacts_dir() if generated else theater.playlists_dir() / "Quiet evening"
    root.mkdir(parents=True)
    (root / "A song.mp3").write_bytes(b"audio")
    nested = root / "nested"
    nested.mkdir()
    (nested / "Second#song.aac").write_bytes(b"audio")
    (root / "description.txt").write_text("Playlist description")
    group = playlists(theater)[0]
    expected_urls = [
        theater.get_url_for_path(str(root / "A song.mp3")),
        theater.get_url_for_path(str(nested / "Second#song.aac")),
    ] if generated else [
        "/theaters/asset-stage/playlists/Quiet%20evening/A%20song.mp3",
        "/theaters/asset-stage/playlists/Quiet%20evening/nested/Second%23song.aac",
    ]
    state = MagicMock()
    notify = MagicMock()
    state.audio = AudioState(notify)
    state.audio.update_music("Previous", ["/previous.mp3"])
    state.audio.music_paused = True
    state.audio.set_pinned(True)
    request = Request({"type": "http", "headers": []})
    mock_session = MagicMock()
    mock_session.is_alive = True
    with patch.object(theaters, "theater_manager", manager), \
         patch.object(theaters, "_require_asset_orator", AsyncMock()), \
         patch.object(theaters.live_agent_manager, "get_session", return_value=mock_session), \
         patch.object(theaters.canvas_states, "get", return_value=state):
        response = await theaters.push_theater_track(theaters.PushAssetRequest(id=group.id), request, "asset-stage")
        with pytest.raises(HTTPException) as missing:
            await theaters.push_theater_track(theaters.PushAssetRequest(id="playlist:missing"), request, "asset-stage")
        assert missing.value.status_code == 404
    assert response["status"] == "ok"
    assert state.audio.current_music_id == group.name
    assert state.audio.current_playlist_tracks == expected_urls
    assert state.audio.music_paused is False
    assert state.audio.pinned is True
    notify.assert_called_with("latest")
    state.persist.assert_called_once()
    mock_session.send_content.assert_called_once()
    call_content, = mock_session.send_content.call_args.args
    assert call_content.role == "system"
    assert call_content.parts[0].text == f"[Orator Action] The active orator changed the music to '{group.name}'."
    assert mock_session.send_content.call_args.kwargs.get("partial") is True


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
