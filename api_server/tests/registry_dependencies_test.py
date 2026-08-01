"""Endpoint-level coverage for late-bound object-registry dependencies."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import object_registry
from api_server import app
from api_server.app import get_current_user, get_theater_owner_credits


def test_current_user_uses_a_database_replaced_in_the_object_registry():
    replacement_db = MagicMock()
    replacement_db.validate_session_token.return_value = {"id": 7, "username": "mocked"}

    with patch.object(object_registry, "db", replacement_db):
        client = TestClient(app)
        response = client.get("/api/auth/me", cookies={"auth_token": "test-token"})

    assert response.status_code == 200
    assert response.json()["user"]["username"] == "mocked"
    replacement_db.validate_session_token.assert_called_once_with("test-token", record_activity=True)


def test_owner_credit_lookup_uses_registry_database_without_reloading_routes():
    replacement_db = MagicMock()
    replacement_db.get_deployment.return_value = {"theater_id": "theater-1", "user_id": 9}
    replacement_db.get_user_by_id.return_value = {"id": 9, "credits": 12.5}

    with patch.object(object_registry, "db", replacement_db):
        assert get_theater_owner_credits("theater-1") == (True, 12.5, 9)

    replacement_db.get_deployment.assert_called_once_with("theater-1")
    replacement_db.get_user_by_id.assert_called_once_with(9)


def test_missing_registry_deployment_is_reported_as_no_available_credits():
    replacement_db = MagicMock()
    replacement_db.get_deployment.return_value = None

    with patch.object(object_registry, "db", replacement_db):
        assert get_theater_owner_credits("missing") == (False, 0.0, None)
