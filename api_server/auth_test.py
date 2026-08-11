"""Unit coverage for authentication routes using registry-backed service doubles."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Response

import object_registry
from api_server import auth
from storage.database import DatabaseConnectionTimeout


def request(*, cookies=None, base_url="http://testserver/"):
    return SimpleNamespace(cookies=cookies or {}, base_url=base_url)


def test_register_creates_session_on_registry_database():
    registry_db = MagicMock()
    registry_db.register_user.return_value = {"id": 4, "username": "ada"}
    registry_db.create_auth_session.return_value = "session-token"
    response = Response()

    with patch.object(object_registry, "db", registry_db):
        result = auth.register_user(auth.RegisterRequest(username="ada", email="a@b.test", password="secret"), response)

    assert result == {"status": "ok", "user": {"id": 4, "username": "ada"}}
    registry_db.register_user.assert_called_once_with("ada", "a@b.test", "secret")
    registry_db.create_auth_session.assert_called_once_with(4)
    assert "auth_token=session-token" in response.headers["set-cookie"]


def test_register_translates_registry_validation_error():
    registry_db = MagicMock()
    registry_db.register_user.side_effect = ValueError("email is taken")
    with patch.object(object_registry, "db", registry_db), pytest.raises(HTTPException, match="email is taken") as error:
        auth.register_user(auth.RegisterRequest(username="ada", email="a@b.test", password="secret"), Response())
    assert error.value.status_code == 400


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
            auth.get_auth_me(request())

    assert error.value.status_code == 503


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
