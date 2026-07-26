import unittest
from fastapi.testclient import TestClient
from web_viewer_app import app, canvas_states

class TestChatPrefixes(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.session_id = "test_prefix_session"
        # Reset chat manager state for test session
        cs = canvas_states.get(self.session_id)
        cs.chat_manager.messages = []

    def test_chat_message_authors_and_retrieval(self):
        # 1. Send message as a custom user/fun username
        user_res = self.client.post(
            f"/api/chat?session_id={self.session_id}",
            json={"author": "Cosmic Voyager 42", "text": "Exploring the canvas!"}
        )
        self.assertEqual(user_res.status_code, 200)

        # 2. Send message as Narratron / agent
        agent_res = self.client.post(
            f"/api/chat?session_id={self.session_id}",
            json={"author": "agent", "text": "Welcome to Narratron!"}
        )
        self.assertEqual(agent_res.status_code, 200)

        # 3. Retrieve messages
        get_res = self.client.get(f"/api/chat?session_id={self.session_id}")
        self.assertEqual(get_res.status_code, 200)
        messages = get_res.json()

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["author"], "Cosmic Voyager 42")
        self.assertEqual(messages[0]["text"], "Exploring the canvas!")
        self.assertEqual(messages[1]["author"], "agent")
        self.assertEqual(messages[1]["text"], "Welcome to Narratron!")

if __name__ == "__main__":
    unittest.main()
