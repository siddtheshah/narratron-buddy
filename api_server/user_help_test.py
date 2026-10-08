"""Verify help authentication, payer isolation, free access, and failures."""

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api_server.shared import app
from api_server import user_help
from services.user_help_service import HelpUnavailableError


@pytest.fixture
def client() -> Iterator[TestClient]:
    with patch.object(user_help, "_public_requests", {}), patch.object(user_help, "_active_public_requests", set()):
        yield TestClient(app)


@pytest.fixture
def database() -> Iterator[MagicMock]:
    database = MagicMock()
    database.get_user_by_id.return_value = {"credits": 3.0}
    database.record_user_usage.return_value = {"credits": 2.85}
    with patch.object(user_help, "db", database), patch.object(user_help, "help_cost", return_value=0.15):
        yield database


def test_canvas_help_bills_requester_and_never_writes_shared_state(client: TestClient, database: MagicMock) -> None:
    with (
        patch.object(user_help, "get_current_user_async", AsyncMock(return_value={"id": 7})),
        patch.object(user_help, "_require_canvas_access_async", AsyncMock()) as access,
        patch.object(user_help, "research", AsyncMock(return_value="Use [Docs](/docs).")),
        patch("object_registry.canvas_states") as states,
        patch.object(user_help.auth_session_cache, "invalidate_user") as invalidate,
    ):
        response = client.post("/api/user-help?theater_id=stage", json={"question": "How?"})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["credits_charged"] == 0.15
    database.get_user_by_id.assert_called_once_with(7)
    assert database.record_user_usage.call_args.args == (7,)
    assert database.record_user_usage.call_args.kwargs["credit_cost"] == 0.15
    assert database.record_user_usage.call_args.kwargs["idempotency_key"].startswith("user-help:")
    invalidate.assert_called_once_with(7)
    access.assert_awaited_once()
    states.assert_not_called()
    states.get.assert_not_called()


@pytest.mark.parametrize("signed_in,credits,status", [(False, 3.0, 401), (True, 0.0, 402)])
def test_canvas_help_rejects_missing_auth_or_balance(
    client: TestClient, database: MagicMock, signed_in: bool, credits: float, status: int,
) -> None:
    database.get_user_by_id.return_value = {"credits": credits}
    with (
        patch.object(user_help, "get_current_user_async", AsyncMock(return_value={"id": 7} if signed_in else None)),
        patch.object(user_help, "_require_canvas_access_async", AsyncMock()),
        patch.object(user_help, "research", AsyncMock()) as research,
    ):
        response = client.post("/api/user-help?theater_id=stage", json={"question": "How?"})
    assert response.status_code == status
    research.assert_not_awaited()
    database.record_user_usage.assert_not_called()


def test_canvas_help_checks_theater_access(client: TestClient, database: MagicMock) -> None:
    with (
        patch.object(user_help, "get_current_user_async", AsyncMock(return_value={"id": 7})),
        patch.object(user_help, "_require_canvas_access_async", AsyncMock(side_effect=HTTPException(403))),
        patch.object(user_help, "research", AsyncMock()) as research,
    ):
        response = client.post("/api/user-help?theater_id=secret", json={"question": "How?"})
    assert response.status_code == 403
    research.assert_not_awaited()
    database.record_user_usage.assert_not_called()


def test_research_failure_is_not_billed(client: TestClient, database: MagicMock) -> None:
    with (
        patch.object(user_help, "get_current_user_async", AsyncMock(return_value={"id": 7})),
        patch.object(user_help, "_require_canvas_access_async", AsyncMock()),
        patch.object(user_help.UserHelpService, "answer", AsyncMock(side_effect=HelpUnavailableError("Unavailable"))),
    ):
        response = client.post("/api/user-help?theater_id=stage", json={"question": "How?"})
    assert response.status_code == 502
    database.record_user_usage.assert_not_called()


def test_public_help_is_free_without_sign_in_and_throttled(client: TestClient, database: MagicMock) -> None:
    with patch.object(user_help, "research", AsyncMock(return_value="See [Docs](/docs).")) as research:
        response = client.post("/api/user-help", json={"question": "What is Narratron?"})
        second = client.post("/api/user-help", json={"question": "What else?"})
    assert response.status_code == 200
    assert response.json()["credits_charged"] == 0.0
    assert second.status_code == 429
    assert second.headers["Retry-After"] == "15"
    research.assert_awaited_once()
    database.get_user_by_id.assert_not_called()
    database.record_user_usage.assert_not_called()


@pytest.mark.parametrize("question,status", [("   ", 400), ("x" * 2001, 422)])
def test_rejects_invalid_questions(client: TestClient, question: str, status: int) -> None:
    with patch.object(user_help, "research", AsyncMock()) as research:
        response = client.post("/api/user-help", json={"question": question})
    assert response.status_code == status
    research.assert_not_awaited()
