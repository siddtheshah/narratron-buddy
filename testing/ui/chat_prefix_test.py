from unittest.mock import patch
from fastapi.testclient import TestClient

from testing.ui.base import UITestCase
from api_server.app import app


class TestChatPrefixes(UITestCase):
    def setUp(self):
        super().setUp()
        access_patcher = patch("api_server.canvas._require_canvas_access")
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

    def test_suggest_prefix_creates_ranked_suggestion_not_plain_chat(self):
        response = self.client.post(
            f"/api/chat?theater_id={self.theater_id}",
            json={"author": "alice", "text": "/suggest Explore the moon"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["type"], "suggestion")
        self.assertEqual(response.json()["suggestion"]["text"], "Explore the moon")

        suggestions = self.client.get(f"/api/suggestions?theater_id={self.theater_id}")
        self.assertEqual(suggestions.status_code, 200)
        self.assertEqual(suggestions.json()[0]["author"], "alice")
        self.assertEqual(suggestions.json()[0]["text"], "Explore the moon")

        chat = self.client.get(f"/api/chat?theater_id={self.theater_id}")
        self.assertEqual(chat.json(), [{
            "author": "alice", "text": "Explore the moon", "type": "suggestion",
        }])

    def test_suggestion_vote_and_withdraw_endpoints(self):
        self.client.post(
            f"/api/chat?theater_id={self.theater_id}",
            json={"author": "alice", "text": "/suggest First idea"},
        )

        vote = self.client.post(
            f"/api/suggestions/upvote?theater_id={self.theater_id}",
            json={"voter": "bob", "target_author": "alice"},
        )
        self.assertEqual(vote.status_code, 200)
        self.assertEqual(vote.json()["type"], "suggestion")
        self.assertEqual(
            self.client.get(f"/api/suggestions?theater_id={self.theater_id}").json()[0]["upvote_count"],
            1,
        )

        withdraw = self.client.post(
            f"/api/suggestions/withdraw?theater_id={self.theater_id}",
            json={"author": "alice"},
        )
        self.assertEqual(withdraw.status_code, 200)
        self.assertEqual(
            self.client.get(f"/api/suggestions?theater_id={self.theater_id}").json(), []
        )
        self.assertEqual(self.client.get(f"/api/chat?theater_id={self.theater_id}").json(), [])

    def test_suggest_without_text_is_rejected(self):
        response = self.client.post(
            f"/api/chat?theater_id={self.theater_id}",
            json={"author": "alice", "text": "/suggest   "},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get(f"/api/suggestions?theater_id={self.theater_id}").json(), [])

    def test_similar_command_is_plain_chat(self):
        response = self.client.post(
            f"/api/chat?theater_id={self.theater_id}",
            json={"author": "alice", "text": "/suggestion is not a command"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["type"], "chat")
        self.assertEqual(self.client.get(f"/api/suggestions?theater_id={self.theater_id}").json(), [])
