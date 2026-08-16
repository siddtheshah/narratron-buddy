"""Coverage for public profiles and owner-only profile settings."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import object_registry
import pytest
from fastapi import HTTPException

from api_server import profiles


def request():
    return SimpleNamespace(cookies={})


def test_get_profile_uses_authenticated_viewer_identity():
    registry_db = MagicMock()
    registry_db.get_user_profile.return_value = {"username": "Ada"}
    with patch.object(object_registry, "db", registry_db), patch.object(profiles, "get_current_user", return_value={"id": 8}):
        assert profiles.get_profile("Ada", request()) == {"username": "Ada"}
    registry_db.get_user_profile.assert_called_once_with("Ada", 8)


def test_get_profile_returns_not_found_for_unknown_user():
    registry_db = MagicMock()
    registry_db.get_user_profile.return_value = None
    with patch.object(object_registry, "db", registry_db), pytest.raises(HTTPException) as error:
        profiles.get_profile("missing", request())
    assert error.value.status_code == 404


def test_update_profile_requires_login_and_updates_owner_settings():
    with patch.object(profiles, "get_current_user", return_value=None), pytest.raises(HTTPException) as error:
        profiles.update_my_profile(profiles.ProfileUpdate(bio="Hi"), request())
    assert error.value.status_code == 401

    registry_db = MagicMock()
    registry_db.get_user_profile.return_value = {"username": "Ada", "is_owner": True}
    with patch.object(object_registry, "db", registry_db), patch.object(profiles, "get_current_user", return_value={"id": 8, "username": "Ada"}):
        result = profiles.update_my_profile(profiles.ProfileUpdate(bio="Hi", stats_visible=True), request())
    assert result["username"] == "Ada"
    registry_db.update_user_profile.assert_called_once_with(8, "Hi", True, "#818cf8")


def test_delete_my_account_requires_login():
    resp = MagicMock()
    with patch.object(profiles, "get_current_user", return_value=None), pytest.raises(HTTPException) as error:
        profiles.delete_my_account(request(), resp)
    assert error.value.status_code == 401


def test_delete_my_account_deletes_user_and_clears_cookie():
    req = SimpleNamespace(cookies={"auth_token": "token_123"})
    resp = MagicMock()
    registry_db = MagicMock()
    with patch.object(object_registry, "db", registry_db), \
         patch.object(profiles, "get_current_user", return_value={"id": 8, "username": "Ada"}), \
         patch.object(profiles.auth_session_cache, "invalidate_token") as mock_inval_tok, \
         patch.object(profiles.auth_session_cache, "invalidate_user") as mock_inval_user:
        result = profiles.delete_my_account(req, resp)

    assert result["status"] == "ok"
    registry_db.invalidate_session_token.assert_called_once_with("token_123")
    mock_inval_tok.assert_called_once_with("token_123")
    mock_inval_user.assert_called_once_with(8)
    registry_db.delete_user.assert_called_once_with(8)
    resp.delete_cookie.assert_called_once_with("auth_token")

