import logging
from typing import Any
from tools.base_tool import BaseTools, with_cooldown

logger = logging.getLogger(__name__)

class ChatTools(BaseTools):
    def __init__(self, config: dict, session_id: str, canvas_state_service: Any = None):
        raw_config = config or {}
        subconfig = raw_config.get("chat", raw_config) if "chat" in raw_config else raw_config
        super().__init__(
            config=subconfig,
            session_id=session_id,
            canvas_state_service=canvas_state_service,
        )
        self.on_send_chat_message = None

    @with_cooldown("sending chat message")
    def send_chat_message(self, text: str) -> str:
        """Sends a text message/response to the user chat window.

        Args:
            text: The text message to send to the user.

        Returns:
            A status message indicating success or failure.
        """
        try:
            logger.info(f"[chat_tool] Sending chat message: {text}")
            if self.canvas_state_service:
                self.canvas_state_service.add_chat_message(text, author="agent", session_id=self.session_id)
            if self.on_send_chat_message:
                self.on_send_chat_message(text)
            return f"Successfully sent chat message to the user: {text}"
        except Exception as e:
            logger.error(f"[chat_tool] Error sending chat message: {e}")
            return f"Error sending chat message: {e}"
