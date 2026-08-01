"""Security-sensitive shared helper coverage."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Response

import object_registry
from api_server import shared


def test_safe_path_param_rejects_traversal_and_unsafe_characters():
    assert shared._safe_path_param("scene 01.png", "filename") == "scene 01.png"
    for candidate in ("", "../secret", "a/b", "name:bad"):
        with pytest.raises(HTTPException) as error:
            shared._safe_path_param(candidate, "filename")
        assert error.value.status_code == 400


def test_canvas_grants_handles_invalid_cookie_and_round_trips_new_grant():
    assert shared._canvas_access_grants(SimpleNamespace(cookies={"canvas_access": "not-base64"})) == {}
    response = Response()
    request = SimpleNamespace(cookies={})
    shared._grant_canvas_access(response, request, "stage", "JOIN")
    encoded = response.headers["set-cookie"].split("canvas_access=")[1].split(";")[0]
    assert json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))) == {"stage": "JOIN"}


def test_current_user_does_not_treat_query_user_id_as_authentication():
    registry_db = MagicMock()
    registry_db.validate_session_token.return_value = None
    request = SimpleNamespace(cookies={"auth_token": "bad"}, query_params={"user_id": "8"})
    with patch.object(object_registry, "db", registry_db):
        assert shared.get_current_user(request) is None
    registry_db.validate_session_token.assert_called_once_with("bad", record_activity=True)
    registry_db.get_user_by_id.assert_not_called()


def test_canvas_access_allows_owner_but_rejects_wrong_join_key():
    deployment = {"theater_id": "stage", "user_id": 3, "join_key": "JOIN"}
    request = SimpleNamespace(cookies={})
    assert shared.can_access_agent_websocket(request, deployment, current_user={"id": 3})
    assert not shared.can_access_agent_websocket(request, deployment, join_key="wrong")


def test_require_canvas_access_uses_registry_deployment_lookup():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = None
    with patch.object(object_registry, "db", registry_db), pytest.raises(HTTPException) as error:
        shared._require_canvas_access(SimpleNamespace(cookies={}, query_params={}), "stage")
    assert error.value.status_code == 404
