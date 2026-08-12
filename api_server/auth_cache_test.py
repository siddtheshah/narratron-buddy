"""Unit coverage for the process-local authentication cache."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from api_server import shared
from api_server.auth_cache import AuthSessionCache, auth_session_cache
from api_server.theater_access_cache import TheaterAccessCache, theater_access_cache


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


def test_theater_access_cache_hashes_join_key_and_invalidates_theater():
    cache = TheaterAccessCache()
    resolver = MagicMock(return_value=({"theater_id": "stage", "join_key": "JOIN"}, True))
    principal = cache.principal_key(user_id=None, join_key="JOIN")

    first = cache.get_or_resolve("stage", principal, resolver)
    second = cache.get_or_resolve("stage", principal, resolver)

    assert first == second == ({"theater_id": "stage", "join_key": "JOIN"}, True)
    resolver.assert_called_once()
    assert principal != "JOIN"
    cache.invalidate_theater("stage")
    assert not cache._entries


def test_theater_access_cache_rechecks_and_denies_after_invalidation():
    cache = TheaterAccessCache()
    principal = cache.principal_key(user_id=8, join_key=None)
    initially_allowed = MagicMock(return_value=({"theater_id": "stage", "user_id": 8}, True))
    now_denied = MagicMock(return_value=({"theater_id": "stage", "user_id": 9}, False))

    assert cache.get_or_resolve("stage", principal, initially_allowed)[1] is True
    cache.invalidate_theater("stage")
    deployment, allowed = cache.get_or_resolve("stage", principal, now_denied)

    assert deployment["user_id"] == 9
    assert allowed is False
    initially_allowed.assert_called_once()
    now_denied.assert_called_once()


def test_canvas_access_check_is_memoized_for_repeated_theater_checks():
    auth_session_cache.clear()
    theater_access_cache.clear()
    request = SimpleNamespace(cookies={"canvas_access": "eyJzdGFnZSI6IkpPSU4ifQ"}, query_params={})
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"theater_id": "stage", "user_id": 3, "join_key": "JOIN"}

    with patch.object(shared, "db", registry_db):
        assert shared._require_canvas_access(request, "stage")["theater_id"] == "stage"
        assert shared._require_canvas_access(request, "stage")["theater_id"] == "stage"

    registry_db.get_deployment.assert_called_once_with("stage")


def test_join_key_viewer_is_denied_after_theater_access_invalidation():
    """A cached grant must not survive a join-key rotation."""
    auth_session_cache.clear()
    theater_access_cache.clear()
    old_grant = "eyJzdGFnZSI6IkpPSU4ifQ"  # base64url({"stage": "JOIN"})
    registry_db = MagicMock()
    registry_db.get_deployment.side_effect = [
        {"theater_id": "stage", "user_id": 3, "join_key": "JOIN"},
        {"theater_id": "stage", "user_id": 3, "join_key": "ROTATED"},
    ]

    with patch.object(shared, "db", registry_db):
        first_request = SimpleNamespace(cookies={"canvas_access": old_grant}, query_params={})
        assert shared._require_canvas_access(first_request, "stage")["theater_id"] == "stage"

        # Simulate the join-key rotation mutation, which clears all cached
        # principals for this theater before the next request arrives.
        theater_access_cache.invalidate_theater("stage")

        second_request = SimpleNamespace(cookies={"canvas_access": old_grant}, query_params={})
        with pytest.raises(shared.HTTPException) as error:
            shared._require_canvas_access(second_request, "stage")

    assert error.value.status_code == 403
    assert registry_db.get_deployment.call_count == 2
