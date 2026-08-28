import base64
import asyncio
import importlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import object_registry
from api_server.app import can_control_agent_websocket, can_access_agent_websocket
app_module = importlib.import_module("api_server.app")


class TestCanAccessAgentWebsocket(unittest.TestCase):
    def test_allows_owner(self):
        deployment = {"theater_id": "s1", "user_id": 42, "join_key": "KEY-123"}
        request = SimpleNamespace(cookies={}, user=None)
        current_user = {"id": 42}

        self.assertTrue(can_access_agent_websocket(request, deployment, current_user=current_user))

    def test_allows_join_key_holder(self):
        deployment = {"theater_id": "s1", "user_id": 42, "join_key": "KEY-123"}
        request = SimpleNamespace(cookies={"canvas_access": "eyJzMSI6IktFWS0xMjMifQ=="}, user=None)
        current_user = {"id": 7}

        self.assertTrue(can_access_agent_websocket(request, deployment, current_user=current_user))

    def test_rejects_unrelated_user_without_join_key(self):
        deployment = {"theater_id": "s1", "user_id": 42, "join_key": "KEY-123"}
        request = SimpleNamespace(cookies={}, user=None)
        current_user = {"id": 7}

        self.assertFalse(can_access_agent_websocket(request, deployment, current_user=current_user))

    def test_allows_join_key_holder_via_websocket_scope(self):
        deployment = {"theater_id": "s1", "user_id": 42, "join_key": "KEY-123"}
        encoded_grants = base64.urlsafe_b64encode(json.dumps({"s1": "KEY-123"}).encode("utf-8")).decode("ascii")
        request = SimpleNamespace(scope={"type": "websocket", "headers": [(b"cookie", f"canvas_access={encoded_grants}".encode("utf-8"))]})
        current_user = {"id": 7}

        self.assertTrue(can_access_agent_websocket(request, deployment, current_user=current_user))


    def test_allows_active_orator(self):
        deployment = {"theater_id": "s1", "user_id": 42, "active_orator_id": 99, "join_key": "KEY-123"}
        request = SimpleNamespace(cookies={}, user=None)
        current_user = {"id": 99}

        self.assertTrue(can_access_agent_websocket(request, deployment, current_user=current_user))

    def test_allows_owner_when_co_orator_is_active(self):
        deployment = {"theater_id": "s1", "user_id": 42, "active_orator_id": 99, "join_key": "KEY-123"}
        request = SimpleNamespace(cookies={}, user=None)
        current_user = {"id": 42}

        self.assertTrue(can_access_agent_websocket(request, deployment, current_user=current_user))


class TestCanControlAgentWebsocket(unittest.TestCase):
    def test_allows_owner_when_no_baton_transfer_is_active(self):
        deployment = {"theater_id": "s1", "user_id": 42, "join_key": "KEY-123"}
        self.assertTrue(can_control_agent_websocket(deployment, current_user={"id": 42}))

    def test_allows_only_active_orator_after_baton_transfer(self):
        deployment = {"theater_id": "s1", "user_id": 42, "active_orator_id": 99, "join_key": "KEY-123"}
        self.assertTrue(can_control_agent_websocket(deployment, current_user={"id": 99}))
        self.assertFalse(can_control_agent_websocket(deployment, current_user={"id": 42}))
        self.assertFalse(can_control_agent_websocket(deployment, current_user={"id": 7}))

    def test_rejects_join_key_holder_without_authenticated_baton(self):
        deployment = {"theater_id": "s1", "user_id": 42, "join_key": "KEY-123"}
        self.assertFalse(can_control_agent_websocket(deployment, current_user=None))


def test_start_agent_stops_registry_session_when_owner_has_no_credits():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 11}
    registry_db.get_user_by_id.return_value = {"id": 11, "credits": 0}
    manager = MagicMock()

    with patch.object(object_registry, "db", registry_db), patch.object(object_registry, "agent_manager", manager):
        response = asyncio.run(app_module.start_agent_endpoint("stage"))

    assert response.status_code == 402
    manager.stop_session.assert_called_once_with(theater_id="stage")


def test_agent_status_reads_active_session_from_registry_manager():
    registry_db = MagicMock()
    registry_db.get_deployment.return_value = {"user_id": 11}
    registry_db.get_user_by_id.return_value = {"id": 11, "credits": 3.5}
    manager = MagicMock()
    manager.get_session.return_value = SimpleNamespace(
        status="running", websocket_connected=True, created_at="now", last_active_at="later"
    )

    with patch.object(object_registry, "db", registry_db), patch.object(object_registry, "agent_manager", manager):
        result = asyncio.run(app_module.get_agent_status_endpoint("stage"))

    assert result["agent_running"] is True
    assert result["websocket_connected"] is True
    assert result["credits"] == 3.5


def test_server_shutdown_closes_database_connection():
    mock_db = MagicMock()
    with patch.object(object_registry, "db", mock_db):
        object_registry.shutdown_database_connection()
    mock_db.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()

