"""Live-agent adapter for the portable Narratron help service."""

from pathlib import Path

from google.adk.sessions import InMemorySessionService

from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from services.user_help_service import (
    HelpUnavailableError,
    UserHelpService,
    ensure_doc_citation,  # noqa: F401 - preserve existing imports
    normalize_doc_links,  # noqa: F401 - preserve existing imports
)
from tools.base_tool import BaseTools, with_cycle_cooldown
from tools.tool_metadata import terminal
from utils.markdown import render_markdown


class UserHelpTool(BaseTools):
    """Publish source-grounded help requested by the theater's live agent."""

    def __init__(
        self,
        theater: Theater,
        canvas_manager: CanvasStateManager,
        model: str,
        help_roots: tuple[Path, ...] | None = None,
        session_service: InMemorySessionService | None = None,
        max_output_tokens: int = 1_200,
    ) -> None:
        super().__init__(theater=theater, canvas_manager=canvas_manager)
        user_help_config = self.config.get("user_help", {})
        self.config = user_help_config if type(user_help_config) is dict else {}
        self.cooldown_duration = float(self.config.get("cooldown_duration", 15.0))
        self.service = UserHelpService(
            model=model, help_roots=help_roots, session_service=session_service,
            max_output_tokens=max_output_tokens,
        )

    @terminal
    @with_cycle_cooldown(action_desc="researching another interface-help question")
    async def user_help_tool(self, question: str) -> str:
        """Answer a Narratron interface question and post the answer to shared chat."""
        if not question.strip():
            return "Please ask a specific question about using the Narratron interface."
        try:
            answer = await self.service.answer(question)
        except HelpUnavailableError as error:
            return str(error)
        chat = self.canvas_manager.chat
        if chat is not None:
            chat.add_message({
                "author": "Narratron User Help",
                "text": answer,
                "html": render_markdown(answer, open_in_new_tab=True),
                "type": "user_help",
            })
            self.canvas_manager.notify_changed("chat")
        return "User help answer posted in chat."
