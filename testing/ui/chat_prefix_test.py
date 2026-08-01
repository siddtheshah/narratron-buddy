from unittest.mock import patch
from fastapi.testclient import TestClient

from testing.ui.base import UITestCase
from api_server.app import app


class TestChatPrefixes(UITestCase):
    def setUp(self):
        super().setUp()
        access_patcher = patch("api_server.app._require_canvas_access")
        access_patcher.start()
        self.addCleanup(access_patcher.stop)
        self.canvas_states = self.isolate_canvas_state_service()
        self.client = TestClient(app)
        self.theater_id = "test_prefix_theater"

    def test_chat_message_authors_and_retrieval(self):
        user_response = self.client.post(
            f"/api/chat?theater_id={self.theater_id}",
            json={"author": "Cosmic Voyager 42", "text": "Exploring the canvas!"},
        )
        self.assertEqual(user_response.status_code, 200)

        agent_response = self.client.post(
            f"/api/chat?theater_id={self.theater_id}",
            json={"author": "agent", "text": "Welcome to Narratron!"},
        )
        self.assertEqual(agent_response.status_code, 200)

        response = self.client.get(f"/api/chat?theater_id={self.theater_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {"author": "Cosmic Voyager 42", "text": "Exploring the canvas!"},
                {"author": "agent", "text": "Welcome to Narratron!"},
            ],
        )
