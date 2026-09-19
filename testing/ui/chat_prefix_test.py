from unittest.mock import MagicMock, patch
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

    def test_chat_message_with_roll_data(self):
        roll_info = {
            "title": "Longsword Attack",
            "character": "Ada",
            "attack_rolls": [{"formula": "1d20 + 5", "result": 18, "total": 23}],
            "damage_rolls": [["Slashing", {"formula": "1d8 + 3", "result": 5, "total": 8}, "1d8 + 3"]],
            "total_damages": {"Total": 8},
        }
        post_res = self.client.post(
            f"/api/chat?theater_id={self.theater_id}",
            json={
                "author": "Ada",
                "text": "🎲 Ada rolled Longsword Attack: [23 to hit] dealing 8 Slashing damage",
                "roll_data": roll_info,
            },
        )
        self.assertEqual(post_res.status_code, 200)
        self.assertEqual(post_res.json()["type"], "chat")

        get_res = self.client.get(f"/api/chat?theater_id={self.theater_id}")
        self.assertEqual(get_res.status_code, 200)
        messages = get_res.json()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["author"], "Ada")
        self.assertEqual(messages[0]["roll_data"], roll_info)

    def test_roll_data_rejected_for_user_without_contributors_permission(self):
        deployment = {
            "theater_id": self.theater_id,
            "user_id": 1,
            "active_orator_id": 1,
            "contributors": "[2, 3]",
        }
        with patch("api_server.canvas.db.get_deployment", return_value=deployment), \
             patch("api_server.canvas.get_current_user", return_value={"id": 99, "username": "intruder"}):
            res = self.client.post(
                f"/api/chat?theater_id={self.theater_id}",
                json={
                    "author": "intruder",
                    "text": "🎲 intruder rolled Stealth: [12]",
                    "roll_data": {"title": "Stealth"},
                },
            )
            self.assertEqual(res.status_code, 403)
            self.assertIn("contributors", res.json()["detail"])

    def test_roll_data_accepted_for_contributors(self):
        deployment = {
            "theater_id": self.theater_id,
            "user_id": 1,
            "active_orator_id": 1,
            "contributors": "[2, 3]",
        }
        with patch("api_server.canvas.db.get_deployment", return_value=deployment), \
             patch("api_server.canvas.get_current_user", return_value={"id": 2, "username": "co_orator"}):
            res = self.client.post(
                f"/api/chat?theater_id={self.theater_id}",
                json={
                    "author": "co_orator",
                    "text": "🎲 co_orator rolled Perception: [18]",
                    "roll_data": {"title": "Perception"},
                },
            )
            self.assertEqual(res.status_code, 200)

    def test_roll_data_forwarded_to_agent_only_when_collab_mode_enabled(self):
        state = self.canvas_states.get(self.theater_id)
        mock_session = MagicMock()
        mock_session.is_alive = True
        deployment = {"theater_id": self.theater_id, "user_id": 1, "contributors": "[]"}

        with patch("api_server.canvas.live_agent_manager.get_session", return_value=mock_session), \
             patch("api_server.canvas.db.get_deployment", return_value=deployment), \
             patch("api_server.canvas.get_current_user", return_value={"id": 1, "username": "Ada"}):
            # 1. Collab mode disabled -> no forward
            state.ui.viewer_collab_enabled = False
            res1 = self.client.post(
                f"/api/chat?theater_id={self.theater_id}",
                json={
                    "author": "Ada",
                    "text": "🎲 Ada rolled d20: [15]",
                    "roll_data": {"title": "Check"},
                },
            )
            self.assertEqual(res1.status_code, 200)
            mock_session.send_user_content.assert_not_called()

            # 2. Collab mode enabled -> forwarded
            state.ui.viewer_collab_enabled = True
            res2 = self.client.post(
                f"/api/chat?theater_id={self.theater_id}",
                json={
                    "author": "Ada",
                    "text": "🎲 Ada rolled d20: [19]",
                    "roll_data": {"title": "Check"},
                },
            )
            self.assertEqual(res2.status_code, 200)
            mock_session.send_user_content.assert_called_once()
            forwarded_text = mock_session.send_user_content.call_args[0][0].parts[0].text
            self.assertIn("[D&D Dice Roll]", forwarded_text)
            self.assertIn("19", forwarded_text)

            # 3. Client already forwarded via agentWs -> not forwarded again
            mock_session.send_user_content.reset_mock()
            res3 = self.client.post(
                f"/api/chat?theater_id={self.theater_id}",
                json={
                    "author": "Ada",
                    "text": "🎲 Ada rolled d20: [20]",
                    "roll_data": {"title": "Check", "forwarded_to_agent": True},
                },
            )
            self.assertEqual(res3.status_code, 200)
            mock_session.send_user_content.assert_not_called()

