import logging
from typing import Any
from tools.base_tool import BaseTools, with_cooldown

logger = logging.getLogger(__name__)

class ChatTools(BaseTools):
    def __init__(self, config: dict, theater_id: str, canvas_state_service: Any = None):
        raw_config = config or {}
        subconfig = raw_config.get("chat", raw_config) if "chat" in raw_config else raw_config
        super().__init__(
            config=subconfig,
            theater_id=theater_id,
            canvas_state_service=canvas_state_service,
        )
        self.on_send_chat_message = None

    @with_cooldown("sending chat message")
    def send_chat_message(self, text: str) -> str:
        """Updates Narratron's pinned current-thought panel.

        Args:
            text: The current thought or status to show to viewers.

        Returns:
            A status message indicating success or failure.
        """
        try:
            logger.debug(f"[ChatTools] Updating agent thought: {text}")
            if self.canvas_state_service:
                self.canvas_state_service.set_agent_thought(text, theater_id=self.theater_id)
            if self.on_send_chat_message:
                self.on_send_chat_message(text)
            return f"Successfully updated the Narratron thought panel: {text}"
        except Exception as e:
            logger.error(f"[ChatTools] Error sending chat message: {e}")
            return f"Error sending chat message: {e}"
