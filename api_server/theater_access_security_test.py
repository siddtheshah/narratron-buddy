"""Regression security tests for vulnerabilities 1 and 2 from AUDIT.md.

Vulnerability 1: Canvas endpoints require an explicit theater_id and authorization.
Default theater fallbacks are eliminated.
Vulnerability 2: Anonymous theater listing is rejected (401) and private state
(canvas_state, config) is stripped from theater listings.
"""

from collections.abc import Iterator
from pathlib import Path
import shutil
import tempfile

import pytest
from fastapi.testclient import TestClient

from api_server.app import app, canvas_states, db, theater_manager


@pytest.fixture
def test_environment() -> Iterator[tuple[TestClient, Path, dict[str, str | int]]]:
    test_dir = tempfile.mkdtemp()
    original_base = theater_manager.base_dir
    original_is_live = db.is_live
    original_db_path = db.db_path

    theater_manager.base_dir = Path(test_dir).resolve()
    db.is_live = False
    db.db_path = Path(test_dir) / "test_sec.db"
    db._init_db()

    owner = db.register_user("sec_owner", "sec-owner@example.com", "Password123")
    db.add_user_credits(int(owner["id"]), 50.0, 2.5)

    theater_manager.create_theater("Secret Campaign", "secret_stage")
    theater_manager.deploy_theater("secret_stage")
    db.record_deployment("secret_stage", int(owner["id"]), "KEY-SECRET", cost=5.0)

    state = canvas_states.get("secret_stage")
    state.chat.add_message({"author": "DM", "text": "Super secret plan: the king is an imposter."})
    state.story.story_planning_state = {"sticky_notes": [{"topic": "Secret Map", "info": "Buried under the castle."}]}
    state.persist()

    client = TestClient(app)
    try:
        yield client, Path(test_dir), owner
    finally:
        db.close()
        db.is_live = original_is_live
        db.db_path = original_db_path
        theater_manager.base_dir = original_base
        shutil.rmtree(test_dir, ignore_errors=True)


def test_missing_theater_id_rejected_with_validation_error(
    test_environment: tuple[TestClient, Path, dict[str, str | int]],
) -> None:
    client, _, _ = test_environment
    # Missing required theater_id parameter returns 422
    assert client.get("/api/latest").status_code == 422
    assert client.get("/api/chat").status_code == 422
    assert client.get("/api/sticky-notes").status_code == 422
    assert client.get("/api/suggestions").status_code == 422
    assert client.post("/api/chat", json={"author": "intruder", "text": "hello"}).status_code == 422


def test_empty_theater_id_rejected_with_bad_request(
    test_environment: tuple[TestClient, Path, dict[str, str | int]],
) -> None:
    client, _, _ = test_environment
    # Empty string theater_id returns 400
    assert client.get("/api/latest?theater_id=").status_code == 400
    assert client.get("/api/chat?theater_id=").status_code == 400
    assert client.get("/api/sticky-notes?theater_id=").status_code == 400
    assert client.get("/api/suggestions?theater_id=").status_code == 400
    assert client.post("/api/chat?theater_id=", json={"author": "intruder", "text": "hello"}).status_code == 400


def test_anonymous_request_with_theater_id_rejected_with_forbidden(
    test_environment: tuple[TestClient, Path, dict[str, str | int]],
) -> None:
    client, _, _ = test_environment
    # Unauthenticated requests without join key receive 403 Forbidden
    assert client.get("/api/latest?theater_id=secret_stage").status_code == 403
    assert client.get("/api/chat?theater_id=secret_stage").status_code == 403
    assert client.get("/api/sticky-notes?theater_id=secret_stage").status_code == 403
    assert client.get("/api/suggestions?theater_id=secret_stage").status_code == 403
    assert client.post("/api/chat?theater_id=secret_stage", json={"author": "intruder", "text": "hello"}).status_code == 403


def test_authorized_caller_with_theater_id_and_join_key_can_access_theater(
    test_environment: tuple[TestClient, Path, dict[str, str | int]],
) -> None:
    client, _, _ = test_environment
    join_resp = client.get(
        "/canvas?theater_id=secret_stage&join_key=KEY-SECRET", follow_redirects=False
    )
    assert join_resp.status_code == 303

    chat_resp = client.get("/api/chat?theater_id=secret_stage")
    assert chat_resp.status_code == 200
    messages = chat_resp.json()
    assert len(messages) >= 1
    assert "Super secret plan" in str(messages[0]["text"])

    notes_resp = client.get("/api/sticky-notes?theater_id=secret_stage")
    assert notes_resp.status_code == 200
    assert notes_resp.json()["count"] >= 1

    latest_resp = client.get("/api/latest?theater_id=secret_stage")
    assert latest_resp.status_code == 200


def test_anonymous_theater_listing_returns_401(
    test_environment: tuple[TestClient, Path, dict[str, str | int]],
) -> None:
    client, _, _ = test_environment
    response = client.get("/api/theaters")
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required to view theaters."


def test_authenticated_theater_listing_excludes_canvas_state_and_private_conversations(
    test_environment: tuple[TestClient, Path, dict[str, str | int]],
) -> None:
    client, _, _ = test_environment
    login_resp = client.post(
        "/api/auth/login",
        json={"username_or_email": "sec_owner", "password": "Password123"},
    )
    assert login_resp.status_code == 200

    response = client.get("/api/theaters")
    assert response.status_code == 200
    theaters_list = response.json()
    assert len(theaters_list) == 1
    theater = theaters_list[0]
    assert theater["theater_id"] == "secret_stage"
    assert theater["is_owner"] is True

    # Critical security assertions: canvas_state and config MUST NOT leak
    assert "canvas_state" not in theater
    assert "config" not in theater


def test_authenticated_user_only_sees_own_theaters(
    test_environment: tuple[TestClient, Path, dict[str, str | int]],
) -> None:
    client, _, _ = test_environment
    db.register_user("other_user", "other@example.com", "Password123")
    client.post(
        "/api/auth/login",
        json={"username_or_email": "other_user", "password": "Password123"},
    )

    response = client.get("/api/theaters")
    assert response.status_code == 200
    assert response.json() == []
