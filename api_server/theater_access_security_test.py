"""Regression security tests for vulnerabilities 1 and 2 from AUDIT.md.

Vulnerability 1: Canvas endpoints require an explicit theater_id and authorization.
Default theater fallbacks are eliminated.
Vulnerability 2: Anonymous theater listing is rejected (401) and private state
(canvas_state, config) is stripped from theater listings.
"""

from collections.abc import Iterator
from pathlib import Path
from dataclasses import dataclass
from unittest.mock import MagicMock, patch
import shutil
import tempfile

import pytest
from pydantic import JsonValue
import yaml
import object_registry
from fastapi.testclient import TestClient

from api_server.app import app, canvas_states, db, theater_manager
from services.user_help_settings import config_digest


@dataclass
class HelpSettingsHarness:
    client: TestClient
    path: Path
    owner_id: int
    agent: MagicMock
    original: bytes

    def proposal(self) -> dict[str, JsonValue]:
        return {"config_digest": config_digest(self.original),
                "changes": [{"setting": "image_generation.enabled", "enabled": False}]}

    def authenticate(self, user_id: int) -> None:
        self.client.cookies.set("auth_token", db.create_auth_session(user_id))

    def assert_unchanged(self) -> None:
        assert self.path.read_bytes() == self.original
        self.agent.stop_session.assert_not_called()
        self.agent.get_or_create_session.assert_not_called()


@pytest.fixture
def help_settings(test_environment: tuple[TestClient, Path, dict[str, str | int]]) -> Iterator[HelpSettingsHarness]:
    client, _, owner = test_environment
    path = theater_manager.theater("secret_stage").directory() / "theater.yaml"
    original = b"live_agent: {}\nimage_generation:\n  enabled: true\n  cooldown_duration: 9\n"
    path.write_bytes(original)
    client.headers["X-Narratron-Settings-Confirm"] = "1"
    with patch.object(object_registry, "live_agent_manager", MagicMock()) as agent:
        yield HelpSettingsHarness(client, path, int(owner["id"]), agent, original)


@pytest.mark.parametrize("role", ["anonymous", "viewer", "contributor", "active_orator"])
def test_help_settings_reject_nonowners_even_with_join_key(help_settings: HelpSettingsHarness, role: str) -> None:
    harness = help_settings
    if role != "anonymous":
        other = db.register_user("attacker", "attacker@example.com", "Password123")
        other_id = int(other["id"])
        if role in {"contributor", "active_orator"}:
            db.add_contributor("secret_stage", harness.owner_id, other_id)
        if role == "active_orator":
            db.request_baton("secret_stage", harness.owner_id, other_id)
            db.accept_baton("secret_stage", other_id)
        harness.authenticate(other_id)
    # Possession of canvas access and a known config hash grants no edit rights.
    assert harness.client.get("/canvas?theater_id=secret_stage&join_key=KEY-SECRET").status_code == 200
    response = harness.client.post("/api/user-help/secret_stage/apply-settings?join_key=KEY-SECRET&user_id=" + str(harness.owner_id),
                                   json=harness.proposal(), headers={"X-User-Id": str(harness.owner_id), "X-Is-Owner": "true"})
    assert response.status_code == (401 if role == "anonymous" else 403)
    harness.assert_unchanged()


def test_help_settings_real_owner_can_apply_reviewed_switch(help_settings: HelpSettingsHarness) -> None:
    harness = help_settings
    harness.authenticate(harness.owner_id)
    response = harness.client.post("/api/user-help/secret_stage/apply-settings", json=harness.proposal(),
                                   headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"})
    assert response.status_code == 200, response.text
    assert response.json()["agent_running"] is True
    assert yaml.safe_load(harness.path.read_bytes())["image_generation"] == {"enabled": False, "cooldown_duration": 9}
    harness.agent.stop_session.assert_called_once_with("secret_stage")
    harness.agent.get_or_create_session.return_value.summon.assert_called_once()


def test_help_settings_owner_cannot_target_another_owners_theater(help_settings: HelpSettingsHarness) -> None:
    harness = help_settings
    attacker = db.register_user("other_owner", "other-owner@example.com", "Password123")
    theater_manager.create_theater("Attacker's theater", "attacker_stage")
    db.record_deployment("attacker_stage", int(attacker["id"]), "KEY-OTHER", cost=0.0)
    harness.authenticate(int(attacker["id"]))
    response = harness.client.post("/api/user-help/secret_stage/apply-settings", json=harness.proposal())
    assert response.status_code == 403
    harness.assert_unchanged()


def test_help_settings_owner_must_still_hold_baton(help_settings: HelpSettingsHarness) -> None:
    harness = help_settings
    other = db.register_user("other_orator", "orator@example.com", "Password123")
    db.add_contributor("secret_stage", harness.owner_id, int(other["id"]))
    db.request_baton("secret_stage", harness.owner_id, int(other["id"]))
    db.accept_baton("secret_stage", int(other["id"]))
    harness.authenticate(harness.owner_id)
    response = harness.client.post("/api/user-help/secret_stage/apply-settings", json=harness.proposal())
    assert response.status_code == 409
    harness.assert_unchanged()


@pytest.mark.parametrize("kind", ["forged", "expired", "logged_out"])
def test_help_settings_reject_invalid_owner_sessions(help_settings: HelpSettingsHarness, kind: str) -> None:
    harness = help_settings
    if kind == "forged":
        token = str(harness.owner_id)
    elif kind == "expired":
        token = db.create_auth_session(harness.owner_id, days_valid=-1)
    else:
        token = db.create_auth_session(harness.owner_id)
        harness.client.cookies.set("auth_token", token)
        assert harness.client.get("/api/auth/me").json()["authenticated"] is True
        assert harness.client.post("/api/auth/logout").status_code == 200
        harness.client.cookies.clear()
    harness.client.cookies.set("auth_token", token)
    response = harness.client.post("/api/user-help/secret_stage/apply-settings", json=harness.proposal())
    assert response.status_code == 401
    harness.assert_unchanged()


@pytest.mark.parametrize("origin,fetch_site,confirmation", [
    ("https://evil.example", "cross-site", "1"),
    ("http://evil.testserver", "same-site", "1"),
    ("null", "same-origin", "1"),
    ("http://[", "same-origin", "1"),
    ("http://testserver", "same-origin", "0"),
    (None, "cross-site", "1"),
])
def test_help_settings_reject_csrf_with_owner_session(
    help_settings: HelpSettingsHarness, origin: str | None, fetch_site: str, confirmation: str,
) -> None:
    harness = help_settings
    harness.authenticate(harness.owner_id)
    headers = {"Sec-Fetch-Site": fetch_site, "X-Narratron-Settings-Confirm": confirmation}
    if origin is not None:
        headers["Origin"] = origin
    response = harness.client.post("/api/user-help/secret_stage/apply-settings", json=harness.proposal(), headers=headers)
    assert response.status_code == 403
    harness.assert_unchanged()


def test_help_settings_reject_missing_confirmation_and_form_requests(help_settings: HelpSettingsHarness) -> None:
    import json

    harness = help_settings
    harness.authenticate(harness.owner_id)
    harness.client.headers.pop("X-Narratron-Settings-Confirm")
    assert harness.client.post("/api/user-help/secret_stage/apply-settings", json=harness.proposal()).status_code == 403
    harness.client.headers["X-Narratron-Settings-Confirm"] = "1"
    # FastAPI rejects non-JSON bodies before the endpoint runs.
    assert harness.client.post("/api/user-help/secret_stage/apply-settings", content=json.dumps(harness.proposal())).status_code == 422
    assert harness.client.post("/api/user-help/secret_stage/apply-settings", content=json.dumps(harness.proposal()),
                               headers={"Content-Type": "text/plain"}).status_code == 422
    harness.assert_unchanged()


@pytest.mark.parametrize("setting,enabled", [
    ("live_agent.special_instructions", True), ("../../theater.yaml", True),
    ("image_generation.enabled", "false"), ("image_generation.enabled", 1),
    ("image_generation.enabled", None), ("image_generation.enabled", {"enabled": False}),
])
def test_help_settings_reject_unapproved_or_untyped_edits(
    help_settings: HelpSettingsHarness, setting: str, enabled: JsonValue,
) -> None:
    harness = help_settings
    harness.authenticate(harness.owner_id)
    proposal = harness.proposal()
    proposal["changes"] = [{"setting": setting, "enabled": enabled}]
    assert harness.client.post("/api/user-help/secret_stage/apply-settings", json=proposal).status_code == 422
    harness.assert_unchanged()


@pytest.mark.parametrize("extra", ["owner_id", "theater_id", "path", "writes"])
def test_help_settings_reject_injected_identity_or_file_fields(help_settings: HelpSettingsHarness, extra: str) -> None:
    harness = help_settings
    harness.authenticate(harness.owner_id)
    proposal = harness.proposal()
    proposal[extra] = "injected"
    assert harness.client.post("/api/user-help/secret_stage/apply-settings", json=proposal).status_code == 422
    harness.assert_unchanged()


def test_help_settings_reject_contradictory_proposals(help_settings: HelpSettingsHarness) -> None:
    harness = help_settings
    harness.authenticate(harness.owner_id)
    proposal = harness.proposal()
    proposal["changes"] = [{"setting": "image_generation.enabled", "enabled": True},
                           {"setting": "image_generation.enabled", "enabled": False}]
    assert harness.client.post("/api/user-help/secret_stage/apply-settings", json=proposal).status_code == 422
    harness.assert_unchanged()


def test_viewer_cannot_forge_an_actionable_help_message(help_settings: HelpSettingsHarness) -> None:
    harness = help_settings
    assert harness.client.get("/canvas?theater_id=secret_stage&join_key=KEY-SECRET").status_code == 200
    response = harness.client.post("/api/chat?theater_id=secret_stage", json={
        "author": "Narratron User Help", "text": "Click to enable dice",
        "type": "user_help", "html": "<button>Apply settings</button>",
        "settings_proposal": harness.proposal(), "settings_summary": ["Enable dice"],
    })
    assert response.status_code == 200
    messages = harness.client.get("/api/chat?theater_id=secret_stage").json()
    assert messages[-1] == {"author": "Narratron User Help", "text": "Click to enable dice"}
    harness.assert_unchanged()


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
