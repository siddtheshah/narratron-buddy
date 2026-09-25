import unittest
from unittest.mock import MagicMock

from testing.base import BaseTestCase
from tools.chat_tool import ChatTools

class TestChatTools(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.theater = MagicMock(theater_id="test_theater")
        self.theater.config = MagicMock(return_value={})
        self.canvas_manager = MagicMock()
        self.chat_tools = ChatTools(self.theater, self.canvas_manager)

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

    def test_send_chat_message_cycle_cooldown_schedules_and_updates(self):
        self.chat_tools.cooldown_duration = 10.0
        res1 = self.chat_tools.send_chat_message("Thought 1")
        self.assertIn("Successfully updated the Narratron thought panel: Thought 1", res1)

        res2 = self.chat_tools.send_chat_message("Thought 2")
        self.assertEqual(res2, "Tool 'send_chat_message' scheduled for next cycle when cooldown expires.")

        res3 = self.chat_tools.send_chat_message("Thought 3")
        self.assertEqual(res3, "Tool 'send_chat_message' parameters updated for next cycle.")

        pending = self.chat_tools.get_pending_cycle_call("send_chat_message")
        self.assertIsNotNone(pending)
        self.assertEqual(pending["args"], ("Thought 3",))
        self.chat_tools.cancel_pending_cycle_call("send_chat_message")

    def test_send_chat_message_cycle_cooldown_executes_on_expiry(self):
        import time
        self.chat_tools.cooldown_duration = 0.05
        res1 = self.chat_tools.send_chat_message("First thought")
        self.assertIn("Successfully updated the Narratron thought panel: First thought", res1)

        res2 = self.chat_tools.send_chat_message("Second thought")
        self.assertEqual(res2, "Tool 'send_chat_message' scheduled for next cycle when cooldown expires.")

        time.sleep(0.15)
        self.assertEqual(self.chat_tools.canvas_manager.tool_response.set_agent_thought.call_count, 2)
        self.chat_tools.canvas_manager.tool_response.set_agent_thought.assert_called_with("Second thought")

if __name__ == "__main__":
    unittest.main()

