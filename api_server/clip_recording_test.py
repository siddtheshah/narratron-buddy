"""Only the authenticated baton holder can start a mobile recording."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from api_server import clip_recording


@pytest.mark.parametrize(
    ("origin", "token", "user_id"),
    [
        ("https://other.test", "session", 2),
        ("http://testserver", "", 2),
        ("http://testserver", "session", 1),
    ],
)
def test_clip_recording_rejects_unauthorized_requests(origin: str, token: str, user_id: int) -> None:
    with (
        patch.object(clip_recording, "_require_canvas_access_async", new=AsyncMock(return_value={"user_id": 1, "active_orator_id": 2})),
        patch.object(clip_recording, "get_current_user_async", new=AsyncMock(return_value={"id": user_id})),
        patch.object(clip_recording, "async_playwright") as playwright,
        pytest.raises(WebSocketDisconnect) as error,
        TestClient(clip_recording.app).websocket_connect(
            "/ws/clip-recording?theater_id=stage",
            headers={"origin": origin, "cookie": f"auth_token={token}"},
        ),
    ):
        pytest.fail("The recording socket accepted an unauthorized request.")
    assert error.value.code == 1008
    playwright.assert_not_called()
