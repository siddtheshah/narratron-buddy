import unittest
from unittest.mock import MagicMock

from testing.base import BaseTestCase
from tools.chat_tool import ChatTools

class TestChatTools(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.theater_manager = MagicMock(theater_id="test_theater")
        self.canvas_manager = MagicMock()
        self.chat_tools = ChatTools(config={}, theater_manager=self.theater_manager, canvas_manager=self.canvas_manager)

    def test_send_chat_message_success(self):
        self.chat_tools.canvas_manager = MagicMock()
        mock_cb = MagicMock()
        self.chat_tools.on_send_chat_message = mock_cb

        res = self.chat_tools.send_chat_message("Hello traveler!")
        self.assertIn("Successfully updated the Narratron thought panel: Hello traveler!", res)
        self.chat_tools.canvas_manager.tool_response.set_agent_thought.assert_called_once_with("Hello traveler!")
        mock_cb.assert_called_once_with("Hello traveler!")

    def test_send_chat_message_no_callback(self):
        res = self.chat_tools.send_chat_message("Welcome!")
        self.assertIn("Successfully updated the Narratron thought panel: Welcome!", res)

    def test_send_chat_message_callback_exception(self):
        def failing_cb(text):
            raise RuntimeError("Connection broken")

        self.chat_tools.on_send_chat_message = failing_cb
        res = self.chat_tools.send_chat_message("Test message")
        self.assertIn("Error sending chat message: Connection broken", res)

if __name__ == "__main__":
    unittest.main()
