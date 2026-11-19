from __future__ import annotations

import logging
from collections.abc import Callable

from components.canvas.avatar_state import AvatarActionSpec
from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from providers.decision_provider import (
    ChoiceQuestion,
    DecisionProvider,
    DecisionRequest,
    MAX_CHOICE_OPTIONS,
)
from tools.base_tool import BaseTools, with_cycle_cooldown
from tools.tool_metadata import terminal

logger = logging.getLogger(__name__)


def _format_avatar_action_line(action: AvatarActionSpec, *, idle_action: str) -> str:
    details: list[str] = []
    if action.label and action.label.lower() != action.id.lower():
        details.append(action.label)
    if action.id == idle_action:
        details.append("default idle")
    if action.light_color:
        details.append(f"color {action.light_color}")
    header = f"- {action.id}"
    if details:
        header += f" ({', '.join(details)})"
    if action.description:
        return f"{header}: {action.description}"
    return header


class ChatTools(BaseTools):
    def __init__(
        self,
        theater: Theater,
        canvas_manager: CanvasStateManager,
        decision_provider: DecisionProvider,
    ) -> None:
        super().__init__(
            theater=theater,
            canvas_manager=canvas_manager,
        )
        if decision_provider is None:
            raise ValueError("decision_provider is required.")
        self.decision_provider: DecisionProvider = decision_provider
        raw_chat = self.config.get("chat")
        chat_config = raw_chat if type(raw_chat) is dict else self.config
        self.config = chat_config if type(chat_config) is dict else {}
        self.cooldown_duration: float = float(self.config.get("cooldown_duration", 0.0))
        self.on_send_chat_message: Callable[[str], None] | None = None

    def select_avatar_action(self, text: str) -> str | None:
        """Choose and apply the avatar action matching Narratron's text response."""
        avatar = self.canvas_manager.avatar
        if avatar is None or not avatar.enabled:
            return None
        normalized_text = text.strip()
        if not normalized_text:
            return None
        actions = avatar.actions[:MAX_CHOICE_OPTIONS]
        if not actions:
            return None
        if len(actions) == 1:
            selected_action = actions[0].id
            avatar.set_action(selected_action)
            return selected_action

        action_lines = "\n".join(
            _format_avatar_action_line(action, idle_action=avatar.idle_action)
            for action in actions
        )
        avatar_header = f"Avatar: {avatar.title}\n" if avatar.title else ""
        input_text = (
            f"Narratron's message:\n{normalized_text}\n\n"
            f"{avatar_header}"
            f"Available avatar actions:\n{action_lines}"
        )
        question = ChoiceQuestion(
            name="avatar_action",
            instructions=(
                "Select the single avatar action ID that best expresses the tone, emotion, "
                "and intent of Narratron's message."
            ),
            choices=tuple(action.id for action in actions),
        )
        result = self.decision_provider.decide(
            DecisionRequest(input_text=input_text, questions=(question,))
        )
        selected_action = result.get_choice("avatar_action").choice
        avatar.set_action(selected_action)
        return selected_action

    @terminal
    @with_cycle_cooldown(action_desc="sending chat message")
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
            if self.on_send_chat_message is not None:
                self.on_send_chat_message(text)
            self.select_avatar_action(text)
            return f"Successfully updated the Narratron thought panel: {text}"
        except Exception as e:
            logger.error(f"[ChatTools] Error sending chat message: {e}")
            return f"Error sending chat message: {e}"
