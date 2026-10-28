"""Authorization and state contract for the phone remote."""

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api_server import remote


@pytest.fixture
def remote_services() -> Iterator[tuple[TestClient, MagicMock, MagicMock]]:
    deployment = {"user_id": 1, "active_orator_id": 2}
    state = MagicMock()
    state.visual.pinned = True
    state.audio.pinned = False
    state.audio.current_music_id = "rain"
    state.audio.current_playlist_tracks = ["/audio/rain.mp3"]
    state.audio.music_paused = False
    state.story.committed_scene_speech_generation = 3
    state.ui.viewer_collab_enabled = True
    state.visual.preview_image_url.return_value = "/theaters/stage/output/scene.webp"
    manager = MagicMock()
    manager.get_session.return_value = None
    with patch.object(remote, "_require_canvas_access", return_value=deployment), \
            patch.object(remote, "get_current_user", return_value={"id": 2}) as current_user, \
            patch.object(remote.db, "get_deployment", return_value=deployment), \
            patch.object(remote.canvas_states, "get", return_value=state), \
            patch.object(remote.theater_manager, "get_theater", return_value=MagicMock(config={})), \
            patch.object(remote, "live_agent_manager", manager):
        yield TestClient(remote.app), current_user, manager


def test_remote_shell_requires_authorized_theater(
    remote_services: tuple[TestClient, MagicMock, MagicMock],
) -> None:
    client, _, _ = remote_services
    response = client.get("/remote?theater_id=stage")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert 'id="controls" disabled' in response.text
    assert 'name="robots" content="noindex, nofollow"' in response.text
    assert 'id="theater"' not in response.text


@pytest.mark.parametrize("query", ["", "?theater_id=", "?theater_id=bad/path"])
def test_remote_page_requires_theater_parameter(query: str) -> None:
    assert TestClient(remote.app).get(f"/remote{query}").status_code == 422


def test_remote_page_requires_login_before_serving_controls(
    remote_services: tuple[TestClient, MagicMock, MagicMock],
) -> None:
    client, current_user, _ = remote_services
    current_user.return_value = None
    response = client.get("/remote?theater_id=stage")
    assert response.status_code == 401
    assert "Sign in to use the remote" in response.text
    assert 'id="controls"' not in response.text
    assert 'id="theater"' not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_remote_page_allows_viewer_without_contributor_permission(
    remote_services: tuple[TestClient, MagicMock, MagicMock],
) -> None:
    client, current_user, _ = remote_services
    current_user.return_value = {"id": 3}
    response = client.get("/remote?theater_id=stage")
    assert response.status_code == 200
    assert 'data-pure-viewer="true"' in response.text


def test_remote_returns_state_for_active_orator(
    remote_services: tuple[TestClient, MagicMock, MagicMock],
) -> None:
    client, _, manager = remote_services
    response = client.get("/api/theaters/stage/remote")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "theater_id": "stage", "agent_running": False, "pinned": True,
        "music_pinned": False, "viewer_collab_enabled": True,
        "is_adventure_mode": False,
        "preview_url": "/theaters/stage/output/scene.webp",
        "music": {"music_id": "rain", "tracks": ["/audio/rain.mp3"], "paused": False},
        "committed_scene_speech_generation": 3,
        "is_contributor": True,
        "is_owner": False,
        "is_orator": True,
    }
    manager.get_session.return_value = MagicMock(is_alive=True)
    assert client.get("/api/theaters/stage/remote").json()["agent_running"] is True


def test_remote_allows_owner_after_handoff_and_pure_viewers(
    remote_services: tuple[TestClient, MagicMock, MagicMock],
) -> None:
    client, current_user, _ = remote_services
    # Owner after handoff can access remote to view Collab and take back baton
    current_user.return_value = {"id": 1}
    res_owner = client.get("/api/theaters/stage/remote")
    assert res_owner.status_code == 200
    assert res_owner.json()["is_contributor"] is True
    assert res_owner.json()["is_owner"] is True
    assert res_owner.json()["is_orator"] is False

    # Viewer without contributor permissions is allowed and flagged as non-contributor
    current_user.return_value = {"id": 3}
    res_viewer = client.get("/api/theaters/stage/remote")
    assert res_viewer.status_code == 200
    assert res_viewer.json()["is_contributor"] is False
    assert res_viewer.json()["is_owner"] is False
    assert res_viewer.json()["is_orator"] is False


def test_remote_rejects_requests_without_canvas_access(
    remote_services: tuple[TestClient, MagicMock, MagicMock],
) -> None:
    client, _, _ = remote_services
    from fastapi import HTTPException
    with patch.object(
        remote,
        "_require_canvas_access",
        side_effect=HTTPException(status_code=403, detail="A valid join key is required to access this theater."),
    ):
        assert client.get("/remote?theater_id=stage").status_code == 403
        assert client.get("/api/theaters/stage/remote").status_code == 403


def test_remote_requires_login(
    remote_services: tuple[TestClient, MagicMock, MagicMock],
) -> None:
    client, current_user, manager = remote_services
    current_user.return_value = None
    assert client.get("/api/theaters/stage/remote").status_code == 401
    manager.get_session.assert_not_called()


def test_remote_returns_empty_preview_when_canvas_has_no_scene(
    remote_services: tuple[TestClient, MagicMock, MagicMock],
) -> None:
    client, _, _ = remote_services
    with patch.object(remote.canvas_states.get("stage").visual, "preview_image_url", return_value=None):
        response = client.get("/api/theaters/stage/remote")
    assert response.status_code == 200
    assert response.json()["preview_url"] is None


@pytest.mark.parametrize(
    "malicious_join_key",
    [
        '" onload="window.auditRemote=1" data-unused="',
        "<script>window.auditRemote=1</script>",
        "' onmouseover='window.auditRemote=1'",
    ],
)
@pytest.mark.parametrize("user_id", [1, 2])
def test_remote_never_reflects_unvalidated_join_key_for_owner_or_orator(
    remote_services: tuple[TestClient, MagicMock, MagicMock],
    malicious_join_key: str,
    user_id: int,
) -> None:
    client, current_user, _ = remote_services
    current_user.return_value = {"id": user_id}
    deployment_with_key = {"user_id": 1, "active_orator_id": 2, "join_key": "STAGE-SECRET"}
    with patch.object(remote, "_require_canvas_access", return_value=deployment_with_key):
        response = client.get(
            "/remote",
            params={"theater_id": "stage", "join_key": malicious_join_key},
        )
    assert response.status_code == 200
    assert "window.auditRemote" not in response.text
    assert 'onload="' not in response.text
    assert "data-unused=" not in response.text
    assert "<script>window.auditRemote" not in response.text
    assert 'data-join-key="STAGE-SECRET"' in response.text
    assert "canvas_access" not in response.cookies


def test_remote_omits_join_key_attribute_when_deployment_has_no_authoritative_key(
    remote_services: tuple[TestClient, MagicMock, MagicMock],
) -> None:
    client, current_user, _ = remote_services
    current_user.return_value = {"id": 1}
    response = client.get(
        "/remote",
        params={"theater_id": "stage", "join_key": '" onload="window.auditRemote=1" data-unused="'},
    )
    assert response.status_code == 200
    assert "window.auditRemote" not in response.text
    assert "data-join-key=" not in response.text
    assert 'data-is-owner="true"' in response.text
    assert 'data-owner-idle="true"' in response.text


def test_remote_renders_authoritative_join_key_and_grants_cookie_when_valid(
    remote_services: tuple[TestClient, MagicMock, MagicMock],
) -> None:
    client, current_user, _ = remote_services
    current_user.return_value = {"id": 3}
    deployment_with_key = {"user_id": 1, "active_orator_id": 2, "join_key": "STAGE-SECRET"}
    with patch.object(remote, "_require_canvas_access", return_value=deployment_with_key):
        response = client.get(
            "/remote",
            params={"theater_id": "stage", "join_key": "stage-secret"},
        )
    assert response.status_code == 200
    assert 'data-pure-viewer="true"' in response.text
    assert 'data-join-key="STAGE-SECRET"' in response.text
    assert 'data-join-key="stage-secret"' not in response.text
    assert "canvas_access" in response.cookies


def test_remote_escapes_html_characters_in_authoritative_join_key(
    remote_services: tuple[TestClient, MagicMock, MagicMock],
) -> None:
    client, current_user, _ = remote_services
    current_user.return_value = {"id": 1}
    deployment_with_quotes = {
        "user_id": 1,
        "active_orator_id": 1,
        "join_key": 'KEY" onload="window.auditRemote=1',
    }
    with patch.object(remote, "_require_canvas_access", return_value=deployment_with_quotes):
        response = client.get("/remote", params={"theater_id": "stage"})
    assert response.status_code == 200
    assert 'onload="window.auditRemote=1"' not in response.text
    assert 'data-join-key="KEY&#34; onload=&#34;window.auditRemote=1"' in response.text

