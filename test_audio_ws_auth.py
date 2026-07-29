import base64
import json
import unittest
from types import SimpleNamespace

from web_viewer_app import can_access_agent_websocket


class TestCanAccessAgentWebsocket(unittest.TestCase):
    def test_allows_owner(self):
        deployment = {"session_id": "s1", "user_id": 42, "join_key": "KEY-123"}
        request = SimpleNamespace(cookies={}, user=None)
        current_user = {"id": 42}

        self.assertTrue(can_access_agent_websocket(request, deployment, current_user=current_user))

    def test_allows_join_key_holder(self):
        deployment = {"session_id": "s1", "user_id": 42, "join_key": "KEY-123"}
        request = SimpleNamespace(cookies={"canvas_access": "eyJzMSI6IktFWS0xMjMifQ=="}, user=None)
        current_user = {"id": 7}

        self.assertTrue(can_access_agent_websocket(request, deployment, current_user=current_user))

    def test_rejects_unrelated_user_without_join_key(self):
        deployment = {"session_id": "s1", "user_id": 42, "join_key": "KEY-123"}
        request = SimpleNamespace(cookies={}, user=None)
        current_user = {"id": 7}

        self.assertFalse(can_access_agent_websocket(request, deployment, current_user=current_user))

    def test_allows_join_key_holder_via_websocket_scope(self):
        deployment = {"session_id": "s1", "user_id": 42, "join_key": "KEY-123"}
        encoded_grants = base64.urlsafe_b64encode(json.dumps({"s1": "KEY-123"}).encode("utf-8")).decode("ascii")
        request = SimpleNamespace(scope={"type": "websocket", "headers": [(b"cookie", f"canvas_access={encoded_grants}".encode("utf-8"))]})
        current_user = {"id": 7}

        self.assertTrue(can_access_agent_websocket(request, deployment, current_user=current_user))


    def test_allows_active_orator(self):
        deployment = {"session_id": "s1", "user_id": 42, "active_orator_id": 99, "join_key": "KEY-123"}
        request = SimpleNamespace(cookies={}, user=None)
        current_user = {"id": 99}

        self.assertTrue(can_access_agent_websocket(request, deployment, current_user=current_user))

    def test_rejects_owner_when_co_orator_is_active(self):
        deployment = {"session_id": "s1", "user_id": 42, "active_orator_id": 99, "join_key": "KEY-123"}
        request = SimpleNamespace(cookies={}, user=None)
        current_user = {"id": 42}

        self.assertFalse(can_access_agent_websocket(request, deployment, current_user=current_user))


if __name__ == "__main__":
    unittest.main()

