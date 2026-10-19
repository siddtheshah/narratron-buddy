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
    state.ui.viewer_collab_enabled = True
    manager = MagicMock()
    manager.get_session.return_value = None
    with patch.object(remote, "_require_canvas_access", return_value=deployment), \
            patch.object(remote, "get_current_user", return_value={"id": 2}) as current_user, \
            patch.object(remote.db, "get_deployment", return_value=deployment), \
            patch.object(remote.canvas_states, "get", return_value=state), \
            patch.object(remote, "live_agent_manager", manager):
        yield TestClient(remote.app), current_user, manager


def test_remote_shell_contains_no_private_state() -> None:
    response = TestClient(remote.app).get("/remote")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert 'id="controls" disabled' in response.text
    assert 'name="robots" content="noindex, nofollow"' in response.text


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
    }
    manager.get_session.return_value = MagicMock(is_alive=True)
    assert client.get("/api/theaters/stage/remote").json()["agent_running"] is True


@pytest.mark.parametrize("user_id", [1, 3])
def test_remote_rejects_owner_after_handoff_and_viewers(
    remote_services: tuple[TestClient, MagicMock, MagicMock], user_id: int,
) -> None:
    client, current_user, manager = remote_services
    current_user.return_value = {"id": user_id}
    assert client.get("/api/theaters/stage/remote").status_code == 403
    manager.get_session.assert_not_called()


def test_remote_requires_login(
    remote_services: tuple[TestClient, MagicMock, MagicMock],
) -> None:
    client, current_user, manager = remote_services
    current_user.return_value = None
    assert client.get("/api/theaters/stage/remote").status_code == 401
    manager.get_session.assert_not_called()
