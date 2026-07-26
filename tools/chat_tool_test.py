import unittest
from unittest.mock import MagicMock

from testing.base import BaseTestCase
from tools.chat_tool import ChatTools

class TestChatTools(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.chat_tools = ChatTools(config={}, session_id="test_session")

    def test_send_chat_message_success(self):
        mock_cb = MagicMock()
        self.chat_tools.on_send_chat_message = mock_cb

        res = self.chat_tools.send_chat_message("Hello traveler!")
        self.assertIn("Successfully sent chat message to the user: Hello traveler!", res)
        mock_cb.assert_called_once_with("Hello traveler!")

    def test_send_chat_message_no_callback(self):
        res = self.chat_tools.send_chat_message("Welcome!")
        self.assertIn("Successfully sent chat message to the user: Welcome!", res)

    def test_send_chat_message_callback_exception(self):
        def failing_cb(text):
            raise RuntimeError("Connection broken")

        self.chat_tools.on_send_chat_message = failing_cb
        res = self.chat_tools.send_chat_message("Test message")
        self.assertIn("Error sending chat message: Connection broken", res)

if __name__ == "__main__":
    unittest.main()
