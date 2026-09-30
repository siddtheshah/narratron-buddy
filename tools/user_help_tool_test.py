from pathlib import Path
from unittest.mock import patch

from tools.user_help_tool import UserHelpTool


def make_tool(tmp_path: Path) -> UserHelpTool:
    with (
        patch("tools.user_help_tool.Agent"),
        patch("tools.user_help_tool.App"),
        patch("tools.user_help_tool.Runner"),
    ):
        return UserHelpTool(model="test-model", help_roots=(tmp_path / "templates", tmp_path / "docs"))


def test_user_help_agent_browses_matching_template_and_documentation_files(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    docs = tmp_path / "docs"
    templates.mkdir()
    docs.mkdir()
    (templates / "canvas.html").write_text(
        '<button id="config-modal-save-btn">Save theater.yaml</button>', encoding="utf-8"
    )
    (docs / "guide.md").write_text(
        "story_planning:\n  adventure_mode: true", encoding="utf-8"
    )
    tool = make_tool(tmp_path)

    files = tool.list_help_files()
    adventure_matches = tool.search_help_files("adventure_mode")
    save_matches = tool.search_help_files("Save theater.yaml")
    saved_config = tool.read_help_file("templates/canvas.html", start_line=1)

    assert "templates/canvas.html" in files
    assert "docs/guide.md" in files
    assert "docs/guide.md:2" in adventure_matches
    assert "templates/canvas.html:1" in save_matches
    assert "Save theater.yaml" in saved_config


def test_user_help_agent_enforces_bounded_search_and_read_calls(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    docs = tmp_path / "docs"
    templates.mkdir()
    docs.mkdir()
    (templates / "canvas.html").write_text("Adventure Mode", encoding="utf-8")
    tool = make_tool(tmp_path)

    tool.search_help_files("Adventure")
    tool.search_help_files("Adventure")
    tool.search_help_files("Adventure")
    blocked_search = tool.search_help_files("Adventure")
    tool.read_help_file("templates/canvas.html")
    tool.read_help_file("templates/canvas.html")
    tool.read_help_file("templates/canvas.html")
    blocked_read = tool.read_help_file("templates/canvas.html")

    assert blocked_search == "Search limit reached. Read the most relevant result and answer now."
    assert blocked_read == "Read limit reached. Answer using the passages already gathered."


def test_user_help_tool_resets_browse_limits_for_each_agent_question(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    docs = tmp_path / "docs"
    templates.mkdir()
    docs.mkdir()
    tool = make_tool(tmp_path)
    tool._search_calls = 3

    with patch.object(tool, "_run_agent", return_value="Use Adventure Mode.") as run_agent:
        answer = tool.user_help_tool("How do I turn on Adventure Mode?")

    assert answer == "Use Adventure Mode."
    assert tool._search_calls == 0
    run_agent.assert_called_once_with("How do I turn on Adventure Mode?")


def test_user_help_tool_rejects_an_empty_question_without_running_agent(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    docs = tmp_path / "docs"
    templates.mkdir()
    docs.mkdir()
    tool = make_tool(tmp_path)

    with patch.object(tool, "_run_agent") as run_agent:
        answer = tool.user_help_tool("   ")

    assert answer == "Please ask a specific question about using the Narratron interface."
    run_agent.assert_not_called()
