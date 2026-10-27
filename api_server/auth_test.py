"""Unit coverage for authentication routes using registry-backed service doubles."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from absl.testing import flagsaver
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from pydantic import JsonValue

import object_registry
from api_server import auth
from storage.database import DatabaseConnectionTimeout
from utils.email_service import EmailSendResult


def request(*, cookies=None, base_url="http://testserver/"):
    return SimpleNamespace(cookies=cookies or {}, base_url=base_url)


def test_register_creates_session_on_registry_database() -> None:
    registry_db = MagicMock()
    registry_db.register_user.return_value = {"id": 4, "username": "ada"}
    registry_db.create_auth_session.return_value = "session-token"
    response = Response()

    with patch.object(object_registry, "db", registry_db):
        result = auth.register_user(auth.RegisterRequest(username="ada", email="a@b.test", password="secret", age_attested=True), response)

    assert result == {"status": "ok", "user": {"id": 4, "username": "ada"}}
    registry_db.register_user.assert_called_once_with("ada", "a@b.test", "secret", age_attested=True)
    registry_db.create_auth_session.assert_called_once_with(4)
    assert "auth_token=session-token" in response.headers["set-cookie"]


def test_register_translates_registry_validation_error() -> None:
    registry_db = MagicMock()
    registry_db.register_user.side_effect = ValueError("email is taken")
    with patch.object(object_registry, "db", registry_db), pytest.raises(HTTPException, match="email is taken") as error:
        auth.register_user(auth.RegisterRequest(username="ada", email="a@b.test", password="secret", age_attested=True), Response())
    assert error.value.status_code == 400


@pytest.mark.parametrize(
    "invalid_username",
    ["", "  ", "ab", "  ad  ", "u" * 51],
)
def test_register_rejects_invalid_username_length(invalid_username: str) -> None:
    registry_db = MagicMock()
    with patch.object(object_registry, "db", registry_db), pytest.raises(HTTPException) as error:
        auth.register_user(
            auth.RegisterRequest(username=invalid_username, email="a@b.test", password="secret", age_attested=True),
            Response(),
        )
    assert error.value.status_code == 400
    assert "between 3 and 50 characters" in str(error.value.detail)
    registry_db.register_user.assert_not_called()


@pytest.mark.parametrize(
    "invalid_password",
    ["", "12345", "p" * 129],
)
def test_register_rejects_invalid_password_length(invalid_password: str) -> None:
    registry_db = MagicMock()
    with patch.object(object_registry, "db", registry_db), pytest.raises(HTTPException) as error:
        auth.register_user(
            auth.RegisterRequest(username="validuser", email="a@b.test", password=invalid_password, age_attested=True),
            Response(),
        )
    assert error.value.status_code == 400
    assert "between 6 and 128 characters" in str(error.value.detail)
    registry_db.register_user.assert_not_called()


@pytest.mark.parametrize("attestation", [{}, {"age_attested": False}, {"age_attested": "true"}, {"age_attested": 1}, {"age_attested": None}])
def test_register_rejects_missing_false_or_non_boolean_attestation(attestation: dict[str, JsonValue]) -> None:
    test_app = FastAPI()
    test_app.post("/register")(auth.register_user)
    registry_db = MagicMock()
    with patch.object(object_registry, "db", registry_db), TestClient(test_app) as client:
        result = client.post("/register", json={"username": "ada", "email": "a@b.test", "password": "secret", **attestation})
    assert result.status_code in {400, 422}
    registry_db.register_user.assert_not_called()
    registry_db.create_auth_session.assert_not_called()
    assert "set-cookie" not in result.headers


def test_login_rejects_unknown_user_without_creating_a_session():
    registry_db = MagicMock()
    registry_db.authenticate_user.return_value = None
    with patch.object(object_registry, "db", registry_db), pytest.raises(HTTPException) as error:
        auth.login_user(auth.LoginRequest(username_or_email="none", password="bad"), Response())
    assert error.value.status_code == 401
    registry_db.create_auth_session.assert_not_called()


def test_logout_invalidates_present_cookie_and_always_clears_it():
    registry_db = MagicMock()
    response = Response()
    with patch.object(object_registry, "db", registry_db):
        assert auth.logout_user(request(cookies={"auth_token": "old"}), response) == {"status": "ok"}
    registry_db.invalidate_session_token.assert_called_once_with("old")
    assert "auth_token=\"\"" in response.headers["set-cookie"]


def test_auth_me_returns_a_timeout_response_when_no_connection_is_available():
    with patch.object(auth, "get_current_user", side_effect=DatabaseConnectionTimeout("pool busy")):
        with pytest.raises(HTTPException) as error:
            auth.get_auth_me(request(), Response())

    assert error.value.status_code == 503


@pytest.mark.parametrize("local_mode", [False, True])
def test_auth_me_returns_server_run_id_and_testing_flag(local_mode):
    registry_db = MagicMock()
    response = Response()
    with flagsaver.flagsaver(testing_use_local=local_mode), patch.object(
        object_registry, "db", registry_db
    ), patch.object(auth, "get_current_user", return_value=None):
        result = auth.get_auth_me(request(), response)

    assert result["authenticated"] is False
    assert result["user"] is None
    assert result["testing_use_local"] is local_mode
    assert isinstance(result["server_run_id"], str) and len(result["server_run_id"]) > 0
    registry_db.authenticate_user.assert_not_called()
    registry_db.create_auth_session.assert_not_called()
    assert "set-cookie" not in response.headers


@flagsaver.flagsaver(testing_use_local=True)
def test_auto_login_endpoint_in_local_mode():
    registry_db = MagicMock()
    user = {"id": 7, "username": "localtest"}
    registry_db.authenticate_user.return_value = user
    registry_db.create_auth_session.return_value = "local-session"
    response = Response()
    with patch.object(object_registry, "db", registry_db):
        result = auth.auto_login_user(response)

    assert result["status"] == "ok"
    assert result["user"] == user
    registry_db.authenticate_user.assert_called_once_with("localtest", "narratron")
    registry_db.create_auth_session.assert_called_once_with(7)
    assert "auth_token=local-session" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]


@flagsaver.flagsaver(testing_use_local=False)
def test_auto_login_endpoint_forbidden_without_testing_flag():
    registry_db = MagicMock()
    response = Response()
    with patch.object(object_registry, "db", registry_db), pytest.raises(HTTPException) as error:
        auth.auto_login_user(response)

    assert error.value.status_code == 403
    registry_db.authenticate_user.assert_not_called()
    registry_db.create_auth_session.assert_not_called()


@flagsaver.flagsaver(testing_use_local=True)
def test_local_auto_login_preserves_existing_session():
    user = {"id": 9, "username": "existing"}
    response = Response()
    with patch.object(object_registry, "db", MagicMock()) as registry_db, patch.object(
        auth, "get_current_user", return_value=user
    ):
        result = auth.get_auth_me(request(), response)

    assert result["user"] == user
    registry_db.authenticate_user.assert_not_called()
    registry_db.create_auth_session.assert_not_called()
    assert "set-cookie" not in response.headers


def test_mic_sensitivity_validates_range_before_writing_registry():
    registry_db = MagicMock()
    with patch.object(object_registry, "db", registry_db), pytest.raises(HTTPException) as error:
        auth.update_mic_sensitivity_endpoint(auth.MicSensitivityRequest(mic_sensitivity=1.1), request())
    assert error.value.status_code == 400
    registry_db.update_user_mic_sensitivity.assert_not_called()


def test_reset_password_rejects_blank_and_delegates_valid_request():
    registry_db = MagicMock()
    with patch.object(object_registry, "db", registry_db), pytest.raises(HTTPException) as error:
        auth.reset_password(auth.ResetPasswordRequest(token="token", new_password="  "))
    assert error.value.status_code == 400

    registry_db.reset_password_with_token.return_value = True
    with patch.object(object_registry, "db", registry_db):
        result = auth.reset_password(auth.ResetPasswordRequest(token="token", new_password="new secret"))
    assert result["status"] == "ok"
    registry_db.reset_password_with_token.assert_called_once_with("token", "new secret")


@pytest.mark.parametrize("delivery", [
    {"sent": False, "method": "failed"},
    {"sent": False, "method": "simulated"},
])
def test_forgot_password_returns_retryable_error_when_email_not_sent(
    delivery: EmailSendResult, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str],
) -> None:
    registry_db = MagicMock()
    registry_db.create_password_reset_token.return_value = (
        "private-reset-token", {"email": "ada@example.test", "username": "ada"},
    )
    test_app = FastAPI()
    test_app.post("/forgot-password")(auth.forgot_password)
    with (
        flagsaver.flagsaver(testing_use_local=False),
        patch.object(object_registry, "db", registry_db),
        patch.object(auth, "send_password_reset_email", return_value=delivery),
        TestClient(test_app) as client,
    ):
        result = client.post("/forgot-password", json={"username_or_email": "ada"})

    assert result.status_code == 503
    assert result.json() == {"detail": "Password reset email could not be sent. Please try again later."}
    assert "email was not sent" in caplog.text
    assert "private-reset-token" not in result.text + caplog.text + capsys.readouterr().out


@pytest.mark.parametrize(("local_mode", "delivery"), [
    (False, {"sent": True, "method": "smtp"}),
    (True, {"sent": False, "method": "simulated"}),
])
def test_forgot_password_accepts_delivery_or_explicit_local_simulation(
    local_mode: bool, delivery: EmailSendResult,
) -> None:
    registry_db = MagicMock()
    registry_db.create_password_reset_token.return_value = (
        "private-reset-token", {"email": "ada@example.test", "username": "ada"},
    )
    test_app = FastAPI()
    test_app.post("/forgot-password")(auth.forgot_password)
    with (
        flagsaver.flagsaver(testing_use_local=local_mode),
        patch.object(object_registry, "db", registry_db),
        patch.object(auth, "send_password_reset_email", return_value=delivery) as send_email,
        TestClient(test_app) as client,
    ):
        result = client.post("/forgot-password", json={"username_or_email": "ada"})

    assert result.status_code == 200
    assert result.json()["status"] == "ok"
    assert "private-reset-token" not in result.text
    send_email.assert_called_once_with(
        "ada@example.test", "ada", "http://testserver/deploy?reset_token=private-reset-token",
    )


def test_forgot_password_keeps_unknown_account_response_generic() -> None:
    registry_db = MagicMock()
    registry_db.create_password_reset_token.return_value = None
    test_app = FastAPI()
    test_app.post("/forgot-password")(auth.forgot_password)
    with (
        patch.object(object_registry, "db", registry_db),
        patch.object(auth, "send_password_reset_email") as send_email,
        TestClient(test_app) as client,
    ):
        result = client.post("/forgot-password", json={"username_or_email": "unknown"})

    assert result.status_code == 200
    assert result.json() == {
        "status": "ok",
        "message": "If an account with that email or username exists, a password reset link has been sent.",
    }
    send_email.assert_not_called()
