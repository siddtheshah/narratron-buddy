"""Verify model-free basic help and explicit, authenticated paid research."""

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api_server.shared import app
from api_server import user_help
from services.user_help_limits import UserHelpLimits
from services.user_help_service import HelpUnavailableError


@pytest.fixture
def client() -> Iterator[TestClient]:
    with (
        patch.object(user_help, "help_limits", UserHelpLimits()),
        patch.object(user_help, "basic_limits", UserHelpLimits()) as basic,
        patch.object(user_help, "billing_locks", {}),
        patch.object(user_help, "help_cost", return_value=0.15),
    ):
        basic.cooldown_seconds = 0
        yield TestClient(app)


@pytest.fixture
def database() -> Iterator[MagicMock]:
    database = MagicMock()
    database.get_user_by_id.return_value = {"credits": 3.0}
    database.record_user_usage.return_value = {"credits": 2.85}
    with patch.object(user_help, "db", database):
        yield database


@pytest.mark.parametrize("signed_in", [True, False])
def test_basic_help_never_calls_a_model_or_bills(client: TestClient, database: MagicMock, signed_in: bool) -> None:
    with (
        patch.object(user_help, "get_current_user_async", AsyncMock(return_value={"id": 7} if signed_in else None)),
        patch.object(user_help, "_require_canvas_access_async", AsyncMock()) as access,
        patch.object(user_help, "research", AsyncMock()) as research,
        patch("object_registry.canvas_states") as states,
    ):
        response = client.post("/api/user-help?theater_id=stage", json={"question": "What is Narratron?"})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["help_mode"] == "basic"
    assert response.json()["can_personalize"] == signed_in
    assert response.json()["personalized_credit_cost"] == 0.15
    assert response.json()["credits_charged"] == 0.0
    assert "interactive AI narration theater" in response.json()["text"]
    database.get_user_by_id.assert_not_called()
    database.record_user_usage.assert_not_called()
    research.assert_not_awaited()
    access.assert_awaited_once()
    states.get.assert_not_called()


def test_canvas_access_is_still_required(client: TestClient) -> None:
    with patch.object(user_help, "_require_canvas_access_async", AsyncMock(side_effect=HTTPException(403))):
        response = client.post("/api/user-help?theater_id=secret", json={"question": "How?"})
    assert response.status_code == 403


@pytest.mark.parametrize("theater_query", ["", "?theater_id=stage"])
def test_paid_help_bills_requester_only_after_success(client: TestClient, database: MagicMock, theater_query: str) -> None:
    with (
        patch.object(user_help, "get_current_user_async", AsyncMock(return_value={"id": 7})),
        patch.object(user_help, "_require_canvas_access_async", AsyncMock()),
        patch.object(user_help, "research", AsyncMock(return_value="Researched answer [Docs](/docs).")),
        patch.object(user_help.auth_session_cache, "invalidate_user") as invalidate,
    ):
        response = client.post("/api/user-help" + theater_query,
                               json={"question": "How?", "personalized": True, "quoted_credit_cost": 0.15})
    assert response.status_code == 200
    assert response.json()["help_mode"] == "personalized"
    assert response.json()["credits_charged"] == 0.15
    database.get_user_by_id.assert_called_once_with(7)
    assert database.record_user_usage.call_args.args == (7,)
    assert database.record_user_usage.call_args.kwargs["credit_cost"] == 0.15
    assert database.record_user_usage.call_args.kwargs["idempotency_key"].startswith("user-help:")
    invalidate.assert_called_once_with(7)


@pytest.mark.parametrize("signed_in,balance,quote,status", [(False, 3.0, 0.15, 401), (True, 0.0, 0.15, 402), (True, 3.0, 0.01, 409), (True, 3.0, None, 409)])
def test_paid_help_rejects_missing_auth_balance_or_price_acceptance(
    client: TestClient, database: MagicMock, signed_in: bool, balance: float, quote: float | None, status: int,
) -> None:
    database.get_user_by_id.return_value = {"credits": balance}
    with (
        patch.object(user_help, "get_current_user_async", AsyncMock(return_value={"id": 7} if signed_in else None)),
        patch.object(user_help, "research", AsyncMock()) as research,
    ):
        response = client.post("/api/user-help", json={"question": "How?", "personalized": True, "quoted_credit_cost": quote})
    assert response.status_code == status
    research.assert_not_awaited()
    database.record_user_usage.assert_not_called()


def test_research_failure_releases_slot_without_billing(client: TestClient, database: MagicMock) -> None:
    with (
        patch.object(user_help, "get_current_user_async", AsyncMock(return_value={"id": 7})),
        patch.object(user_help.UserHelpService, "answer", AsyncMock(side_effect=HelpUnavailableError("Unavailable"))),
    ):
        response = client.post("/api/user-help", json={"question": "How?", "personalized": True, "quoted_credit_cost": 0.15})
    assert response.status_code == 502
    assert not user_help.help_limits._active
    database.record_user_usage.assert_not_called()


def test_paid_limits_are_shared_across_surfaces_but_do_not_block_basic_help(client: TestClient, database: MagicMock) -> None:
    with (
        patch.object(user_help, "get_current_user_async", AsyncMock(return_value={"id": 7})),
        patch.object(user_help, "_require_canvas_access_async", AsyncMock()),
        patch.object(user_help, "research", AsyncMock(return_value="See [Docs](/docs).")) as research,
    ):
        body = {"question": "How?", "personalized": True, "quoted_credit_cost": 0.15}
        first = client.post("/api/user-help?theater_id=stage", json=body)
        second = client.post("/api/user-help", json=body)
        basic = client.post("/api/user-help", json={"question": "What is Narratron?"})
    assert first.status_code == 200
    assert second.status_code == 429
    assert 1 <= int(second.headers["Retry-After"]) <= 15
    assert basic.status_code == 200
    research.assert_awaited_once()
    database.record_user_usage.assert_called_once()


def test_complex_basic_question_links_docs_without_research(client: TestClient) -> None:
    with (
        patch.object(user_help, "get_current_user_async", AsyncMock(return_value=None)),
        patch.object(user_help, "research", AsyncMock()) as research,
        patch.object(user_help.help_catalog, "answer", return_value="Read [VTT](/docs/virtual-tabletop).") as catalog,
    ):
        response = client.post("/api/user-help", json={"question": "How do I rotate a token?"})
    assert response.status_code == 200
    assert "/docs/virtual-tabletop" in response.json()["html"]
    catalog.assert_called_once_with("How do I rotate a token?")
    research.assert_not_awaited()


def test_help_info_reports_both_tiers(client: TestClient) -> None:
    response = client.get("/api/user-help")
    assert response.json() == {"basic_free": True, "personalized_credit_cost": 0.15}


@pytest.mark.parametrize("question,status", [("   ", 400), ("x" * 2001, 422)])
def test_rejects_invalid_questions(client: TestClient, question: str, status: int) -> None:
    with patch.object(user_help, "research", AsyncMock()) as research:
        response = client.post("/api/user-help", json={"question": question})
    assert response.status_code == status
    research.assert_not_awaited()
