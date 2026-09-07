import logging
from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from tools.base_tool import BaseTools, with_cooldown

logger = logging.getLogger(__name__)

class ChatTools(BaseTools):
    def __init__(self, theater: Theater, canvas_manager: CanvasStateManager):
        super().__init__(
            theater=theater,
            canvas_manager=canvas_manager,
        )
        subconfig = self.config.get("chat", self.config) if "chat" in self.config else self.config
        self.config = subconfig if isinstance(subconfig, dict) else {}
        self.cooldown_duration = float(self.config.get("cooldown_duration", 0.0))
        self.on_send_chat_message = None

    @with_cooldown(action_desc="sending chat message")
    def send_chat_message(self, text: str) -> str:
        """Updates Narratron's pinned current-thought panel.

        Args:
            text: The current thought or status to show to viewers.

        Returns:
            A status message indicating success or failure.
        """
        try:
            logger.debug(f"[ChatTools] Updating agent thought: {text}")
            self.canvas_manager.tool_response.set_agent_thought(text)
            if self.on_send_chat_message:
                self.on_send_chat_message(text)
            return f"Successfully updated the Narratron thought panel: {text}"
        except Exception as e:
            logger.error(f"[ChatTools] Error sending chat message: {e}")
            return f"Error sending chat message: {e}"
