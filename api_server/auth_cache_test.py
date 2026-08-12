"""Unit coverage for the process-local authentication cache."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from api_server import shared
from api_server.auth_cache import AuthSessionCache, auth_session_cache


def test_session_cache_uses_token_digest_and_returns_copies():
    cache = AuthSessionCache()
    validator = MagicMock(return_value={"id": 8, "username": "ada", "expires_at": "2099-01-01T00:00:00+00:00"})

    first = cache.get_or_validate("secret-token", validator)
    second = cache.get_or_validate("secret-token", validator)

    assert first == second == {"id": 8, "username": "ada", "expires_at": "2099-01-01T00:00:00+00:00"}
    assert first is not second
    validator.assert_called_once()
    assert "secret-token" not in cache._entries
    assert len(cache._entries) == 1
    assert cache.hits == 1
    assert cache.misses == 1


def test_session_cache_negative_caches_invalid_tokens():
    cache = AuthSessionCache()
    validator = MagicMock(return_value=None)

    assert cache.get_or_validate("bad-token", validator) is None
    assert cache.get_or_validate("bad-token", validator) is None

    validator.assert_called_once()


def test_invalidating_a_user_removes_all_of_its_session_entries():
    cache = AuthSessionCache()
    validator = MagicMock(return_value={"id": 8, "expires_at": "2099-01-01T00:00:00+00:00"})
    cache.get_or_validate("first", validator)
    cache.get_or_validate("second", validator)

    cache.invalidate_user(8)

    assert not cache._entries
    assert cache.stale_account_invalidations == 2


def test_current_user_is_memoized_on_the_request():
    auth_session_cache.clear()
    request = SimpleNamespace(cookies={"auth_token": "request-token"}, query_params={})
    registry_db = MagicMock()
    registry_db.validate_session_token.return_value = {"id": 8, "username": "ada", "expires_at": "2099-01-01T00:00:00+00:00"}

    with patch.object(shared, "db", registry_db):
        assert shared.get_current_user(request)["username"] == "ada"
        assert shared.get_current_user(request)["username"] == "ada"

    registry_db.validate_session_token.assert_called_once_with("request-token", record_activity=True)
