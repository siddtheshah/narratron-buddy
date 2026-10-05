"""Coverage for public profiles and owner-only profile settings."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import object_registry
import pytest
from fastapi import HTTPException

from api_server import profiles


def request(*, base_url="http://testserver/"):
    return SimpleNamespace(cookies={}, base_url=base_url)


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


def test_create_and_claim_credit_gift_require_auth_and_invalidate_balances():
    registry_db = MagicMock()
    registry_db.create_credit_gift.return_value = {
        "token": "gift-token", "credits": 12.5, "expires_at": "2026-01-01T00:00:00+00:00"
    }
    with patch.object(profiles, "get_current_user", return_value=None), pytest.raises(HTTPException) as error:
        profiles.create_credit_gift(profiles.CreditGiftRequest(credits=12.5), request())
    assert error.value.status_code == 401

    with patch.object(object_registry, "db", registry_db), \
         patch.object(profiles, "get_current_user", return_value={"id": 8}), \
         patch.dict(os.environ, {"PUBLIC_BASE_URL": "https://narratron.example"}):
        result = profiles.create_credit_gift(
            profiles.CreditGiftRequest(credits=12.5), request(base_url="http://testserver/")
        )
    assert result["link"] == "https://narratron.example/gift/gift-token"
    registry_db.create_credit_gift.assert_called_once_with(8, 12.5)

    registry_db.claim_credit_gift.return_value = {"credits": 12.5, "sender_user_id": 3}
    with patch.object(object_registry, "db", registry_db), \
         patch.object(profiles, "get_current_user", return_value={"id": 8}), \
         patch.object(profiles.auth_session_cache, "invalidate_user") as invalidate:
        claimed = profiles.claim_credit_gift("gift-token", request())
    assert claimed == {"status": "claimed", "credits": 12.5}
    invalidate.assert_any_call(8)
    invalidate.assert_any_call(3)


def test_credit_gift_rejects_missing_or_invalid_public_origin():
    registry_db = MagicMock()
    registry_db.create_credit_gift.return_value = {"token": "gift-token", "credits": 1, "expires_at": "now"}
    with patch.object(object_registry, "db", registry_db), \
         patch.object(profiles, "get_current_user", return_value={"id": 8}), \
         patch.dict(os.environ, {"PUBLIC_BASE_URL": "https://attacker.example/path"}):
        with pytest.raises(HTTPException) as error:
            profiles.create_credit_gift(profiles.CreditGiftRequest(credits=1), request())
    assert error.value.status_code == 500


class DummyUploadFile:
    def __init__(self, filename: str, content: bytes) -> None:
        self.filename = filename
        self._content = content

    async def read(self) -> bytes:
        return self._content


def _png_bytes() -> bytes:
    import io
    from PIL import Image
    img = Image.new("RGBA", (50, 50), (255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_get_user_stamps_success() -> None:
    registry_db = MagicMock()
    registry_db.get_user_profile.return_value = {"username": "Ada"}
    registry_db.get_user_by_username.return_value = {"id": 8, "username": "Ada"}
    registry_db.get_user_stamps.return_value = [
        {"id": 1, "user_id": 8, "name": "Star", "filename": "star.png", "content_type": "image/png"}
    ]
    with patch.object(object_registry, "db", registry_db), \
         patch.object(profiles, "get_current_user", return_value={"id": 8}):
        stamps = profiles.get_user_stamps_endpoint("Ada", request())
    assert len(stamps) == 1
    assert stamps[0]["name"] == "Star"
    assert stamps[0]["url"] == "/api/stamps/1"


@pytest.mark.asyncio
async def test_upload_stamp_requires_login() -> None:
    dummy_file = DummyUploadFile("cat.png", _png_bytes())
    with patch.object(profiles, "get_current_user", return_value=None), \
         pytest.raises(HTTPException) as err:
        await profiles.upload_stamp(request(), dummy_file, "Cat")
    assert err.value.status_code == 401


@pytest.mark.asyncio
async def test_upload_stamp_enforces_10_limit() -> None:
    dummy_file = DummyUploadFile("cat.png", _png_bytes())
    registry_db = MagicMock()
    registry_db.count_user_stamps.return_value = 10
    with patch.object(object_registry, "db", registry_db), \
         patch.object(profiles, "get_current_user", return_value={"id": 8}), \
         pytest.raises(HTTPException) as err:
        await profiles.upload_stamp(request(), dummy_file, "Cat")
    assert err.value.status_code == 400
    assert "Maximum limit of 10 stamps reached" in str(err.value.detail)


@pytest.mark.asyncio
async def test_upload_stamp_rejects_empty_file() -> None:
    dummy_file = DummyUploadFile("empty.png", b"")
    registry_db = MagicMock()
    registry_db.count_user_stamps.return_value = 2
    with patch.object(object_registry, "db", registry_db), \
         patch.object(profiles, "get_current_user", return_value={"id": 8}), \
         pytest.raises(HTTPException) as err:
        await profiles.upload_stamp(request(), dummy_file, "Empty")
    assert err.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_stamp_success() -> None:
    dummy_file = DummyUploadFile("star.png", _png_bytes())
    registry_db = MagicMock()
    registry_db.count_user_stamps.return_value = 3
    registry_db.create_user_stamp.return_value = {
        "id": 12,
        "user_id": 8,
        "name": "Star",
        "filename": "abc_star.png",
        "content_type": "image/png",
    }
    registry_storage = MagicMock()
    registry_storage.save_stamp.return_value = "abc_star.png"

    with patch.object(object_registry, "db", registry_db), \
         patch.object(profiles, "stamp_storage", registry_storage), \
         patch.object(profiles, "get_current_user", return_value={"id": 8}), \
         patch.object(profiles.auth_session_cache, "invalidate_user"):
        result = await profiles.upload_stamp(request(), dummy_file, "Star")

    assert result["id"] == 12
    assert result["url"] == "/api/stamps/12"
    registry_storage.save_stamp.assert_called_once()
    registry_db.create_user_stamp.assert_called_once_with(8, "Star", "abc_star.png", "image/png")


def test_delete_stamp_requires_login() -> None:
    with patch.object(profiles, "get_current_user", return_value=None), \
         pytest.raises(HTTPException) as err:
        profiles.delete_stamp_endpoint(5, request())
    assert err.value.status_code == 401


def test_delete_stamp_not_found() -> None:
    registry_db = MagicMock()
    registry_db.get_user_stamp.return_value = None
    with patch.object(object_registry, "db", registry_db), \
         patch.object(profiles, "get_current_user", return_value={"id": 8}), \
         pytest.raises(HTTPException) as err:
        profiles.delete_stamp_endpoint(99, request())
    assert err.value.status_code == 404


def test_delete_stamp_success() -> None:
    registry_db = MagicMock()
    registry_db.get_user_stamp.return_value = {
        "id": 5, "user_id": 8, "name": "Star", "filename": "star.png"
    }
    registry_storage = MagicMock()
    with patch.object(object_registry, "db", registry_db), \
         patch.object(profiles, "stamp_storage", registry_storage), \
         patch.object(profiles, "get_current_user", return_value={"id": 8}), \
         patch.object(profiles.auth_session_cache, "invalidate_user"):
        result = profiles.delete_stamp_endpoint(5, request())
    assert result["status"] == "ok"
    registry_storage.delete_stamp.assert_called_once_with(8, "star.png")
    registry_db.delete_user_stamp.assert_called_once_with(5, 8)


def test_get_stamp_endpoint_success() -> None:
    registry_db = MagicMock()
    registry_db.get_stamp_by_id.return_value = {
        "id": 5, "user_id": 8, "name": "Star", "filename": "star.png", "content_type": "image/png"
    }
    registry_storage = MagicMock()
    registry_storage.read_stamp.return_value = b"\x89PNGfake"
    with patch.object(object_registry, "db", registry_db), \
         patch.object(profiles, "stamp_storage", registry_storage):
        resp = profiles.get_stamp_endpoint(5)
    assert resp.body == b"\x89PNGfake"
    assert resp.media_type == "image/png"


def test_get_stamp_endpoint_not_found() -> None:
    registry_db = MagicMock()
    registry_db.get_stamp_by_id.return_value = None
    with patch.object(object_registry, "db", registry_db), \
         pytest.raises(HTTPException) as err:
        profiles.get_stamp_endpoint(999)
    assert err.value.status_code == 404
