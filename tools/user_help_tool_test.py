import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from tools.user_help_tool import UserHelpTool
from tools.user_help_tool import ensure_doc_citation, normalize_doc_links


def make_tool(tmp_path: Path) -> UserHelpTool:
    theater = MagicMock()
    theater.theater_id = "test_theater"
    theater.config.return_value = {"user_help": {"cooldown_duration": 15}}
    with (
        patch("tools.user_help_tool.Agent"),
        patch("tools.user_help_tool.App"),
        patch("tools.user_help_tool.Runner"),
    ):
        return UserHelpTool(
            theater=theater,
            canvas_manager=MagicMock(),
            model="test-model",
            help_roots=(tmp_path / "templates", tmp_path / "docs"),
        )


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

    with patch.object(tool, "_run_agent", new=AsyncMock(return_value="Use Adventure Mode.")) as run_agent:
        answer = asyncio.run(tool.user_help_tool("How do I turn on Adventure Mode?"))

    assert answer == "User help answer posted in chat."
    assert tool._search_calls == 0
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

    with patch.object(tool, "_run_agent", new=AsyncMock()) as run_agent:
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

    with patch.object(tool, "_run_agent", new=AsyncMock(return_value="Help answer [Docs](/docs).")):
        first = asyncio.run(tool.user_help_tool("How do I open the menu?"))
        second = asyncio.run(tool.user_help_tool("How do I save theater.yaml?"))

    assert first == "User help answer posted in chat."
    assert second == "Tool 'user_help_tool' scheduled for next cycle when cooldown expires."


def test_user_help_tool_normalizes_file_paths_to_web_doc_urls() -> None:
    from tools.user_help_tool import normalize_doc_links

    raw = "Refer to [Virtual Tabletop Guide](virtual_tabletop_guide.md) or [Beyond20](docs/beyond20.md)."
    normalized = normalize_doc_links(raw)
    assert normalized == "Refer to [Virtual Tabletop Guide](/docs/virtual-tabletop) or [Beyond20](/docs/beyond20)."


def test_user_help_tool_ensures_doc_citation_on_all_answers() -> None:
    from tools.user_help_tool import ensure_doc_citation

    # When doc link is already present, it is not modified
    already_cited = "Open settings as documented in [theater.yaml](/docs/theater-yaml)."
    assert ensure_doc_citation(already_cited, "How to configure?") == already_cited

    # When missing, keyword-specific citation is appended
    answer_vtt = ensure_doc_citation("Right click for the action-wheel.", "How to use action wheel?")
    assert "[Virtual Tabletop Guide](/docs/virtual-tabletop)" in answer_vtt

    answer_gen = ensure_doc_citation("Press Tab.", "How to toggle view?")
    assert "[Narratron Documentation](/docs)" in answer_gen


def test_user_help_tool_resolves_files_via_web_doc_urls(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    docs = tmp_path / "docs"
    templates.mkdir()
    docs.mkdir()
    (docs / "virtual_tabletop_guide.md").write_text("# VTT Guide\nGrid and tokens.", encoding="utf-8")
    tool = make_tool(tmp_path)

    # Resolve using doc URL directly
    resolved = tool._resolve_help_path("/docs/virtual-tabletop")
    assert resolved is not None
    assert resolved.name == "virtual_tabletop_guide.md"

    # Reading file using doc URL
    content = tool.read_help_file("/docs/virtual-tabletop", start_line=1)
    assert "Grid and tokens." in content


def test_user_help_tool_agent_instructions_direct_concise_answers_and_doc_citations(tmp_path: Path) -> None:
    tool = make_tool(tmp_path)
    instructions = tool._create_agent().instruction
    assert "simple, answer it concisely" in instructions
    assert "general or broad" in instructions
    assert "refer the user to the relevant documentation guide" in instructions
    assert "Always cite links to the relevant documentation for any answer" in instructions
    assert "/docs/virtual-tabletop" in instructions
    assert "/docs/theater-yaml" in instructions


def test_feedback_guide_is_discoverable_readable_and_citable(tmp_path: Path) -> None:
    tool = make_tool(tmp_path)
    tool.help_roots = tool._default_help_roots()
    assert "docs/theater_feedback_and_reports.md (doc URL: /docs/feedback-and-reporting)" in tool.list_help_files()
    assert "theater_feedback_and_reports.md" in tool.search_help_files("Feedback/Report Theater")
    assert "Send Report" in tool.read_help_file("/docs/feedback-and-reporting")
    assert "/docs/feedback-and-reporting" in tool._create_agent().instruction
    assert normalize_doc_links("[Feedback](docs/theater_feedback_and_reports.md)") == "[Feedback](/docs/feedback-and-reporting)"
    for question in ("How do I report a malicious theater?", "How do I file a bug?", "Where can I suggest an improvement?"):
        assert "[Feedback and Reporting](/docs/feedback-and-reporting)" in ensure_doc_citation("Open the left-hand menu.", question)

