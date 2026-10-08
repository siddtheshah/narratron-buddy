import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from tools.user_help_tool import UserHelpTool


def make_tool(tmp_path: Path) -> UserHelpTool:
    theater = MagicMock()
    theater.theater_id = "test_theater"
    theater.config.return_value = {"user_help": {"cooldown_duration": 15}}
    with (
        patch("services.user_help_service.Agent"),
        patch("services.user_help_service.App"),
        patch("services.user_help_service.Runner"),
    ):
        return UserHelpTool(
            theater=theater,
            canvas_manager=MagicMock(),
            model="test-model",
            help_roots=(tmp_path / "templates", tmp_path / "docs"),
        )


def test_user_help_tool_resets_browse_limits_for_each_agent_question(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    docs = tmp_path / "docs"
    templates.mkdir()
    docs.mkdir()
    tool = make_tool(tmp_path)
    tool.service._search_calls = 3

    with patch.object(tool.service, "_run_agent", new=AsyncMock(return_value="Use Adventure Mode.")) as run_agent:
        answer = asyncio.run(tool.user_help_tool("How do I turn on Adventure Mode?"))

    assert answer == "User help answer posted in chat."
    assert tool.service._search_calls == 0
    run_agent.assert_called_once_with("How do I turn on Adventure Mode?")
    tool.canvas_manager.chat.add_message.assert_called_once_with({
        "author": "Narratron User Help",
        "text": "Use Adventure Mode.\n\nFor more details, see the [Writing Adventures Guide](/docs/writing-adventures).",
        "html": "<p>Use Adventure Mode.</p>\n<p>For more details, see the <a href=\"/docs/writing-adventures\" target=\"_blank\" rel=\"noopener noreferrer\">Writing Adventures Guide</a>.</p>",
        "type": "user_help",
    })
    tool.canvas_manager.notify_changed.assert_called_once_with("chat")


def test_user_help_tool_rejects_an_empty_question_without_running_agent(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    docs = tmp_path / "docs"
    templates.mkdir()
    docs.mkdir()
    tool = make_tool(tmp_path)

    with patch.object(tool.service, "_run_agent", new=AsyncMock()) as run_agent:
        answer = asyncio.run(tool.user_help_tool("   "))

    assert answer == "Please ask a specific question about using the Narratron interface."
    run_agent.assert_not_called()


def test_user_help_tool_schedules_another_question_during_cooldown(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    docs = tmp_path / "docs"
    templates.mkdir()
    docs.mkdir()
    tool = make_tool(tmp_path)
    tool.cooldown_duration = 60.0

    with patch.object(tool.service, "_run_agent", new=AsyncMock(return_value="Help answer [Docs](/docs).")):
        first = asyncio.run(tool.user_help_tool("How do I open the menu?"))
        second = asyncio.run(tool.user_help_tool("How do I save theater.yaml?"))

    assert first == "User help answer posted in chat."
    assert second == "Tool 'user_help_tool' scheduled for next cycle when cooldown expires."
