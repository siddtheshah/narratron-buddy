import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from google.adk.events import Event

from services.user_help_service import HelpUnavailableError, UserHelpService, ensure_doc_citation, normalize_doc_links


def make_service(tmp_path: Path) -> UserHelpService:
    with patch("services.user_help_service.Runner"):
        return UserHelpService(model="test-model", help_roots=(tmp_path / "templates", tmp_path / "docs"))


def test_service_returns_a_cited_answer_without_a_theater(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    with patch.object(service, "_run_agent", AsyncMock(return_value="Read [Docs](docs.html).")) as run:
        answer = asyncio.run(service.answer("  What is Narratron?  "))
    assert answer == "Read [Docs](/docs)."
    run.assert_awaited_once_with("What is Narratron?")


def test_empty_service_question_never_starts_research(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    with patch.object(service, "_run_agent", AsyncMock()) as run:
        with pytest.raises(ValueError):
            asyncio.run(service.answer("   "))
    run.assert_not_awaited()


@pytest.mark.parametrize("failure", [TimeoutError("timeout"), RuntimeError("provider failure")])
def test_provider_failure_raises_instead_of_returning_a_billable_answer(
    tmp_path: Path, failure: Exception,
) -> None:
    service = make_service(tmp_path)

    async def failed_events() -> AsyncIterator[Event]:
        raise failure
        yield Event(author="user_help")

    with patch.object(service._runner, "run_async", return_value=failed_events()):
        with pytest.raises(HelpUnavailableError):
            asyncio.run(service.answer("How?"))


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
    tool = make_service(tmp_path)

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
    tool = make_service(tmp_path)

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


def test_user_help_tool_normalizes_file_paths_to_web_doc_urls() -> None:
    from services.user_help_service import normalize_doc_links

    raw = "Refer to [Virtual Tabletop Guide](virtual_tabletop_guide.md) or [Beyond20](docs/beyond20.md)."
    normalized = normalize_doc_links(raw)
    assert normalized == "Refer to [Virtual Tabletop Guide](/docs/virtual-tabletop) or [Beyond20](/docs/beyond20)."


def test_user_help_tool_ensures_doc_citation_on_all_answers() -> None:
    from services.user_help_service import ensure_doc_citation

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
    tool = make_service(tmp_path)

    # Resolve using doc URL directly
    resolved = tool._resolve_help_path("/docs/virtual-tabletop")
    assert resolved is not None
    assert resolved.name == "virtual_tabletop_guide.md"

    # Reading file using doc URL
    content = tool.read_help_file("/docs/virtual-tabletop", start_line=1)
    assert "Grid and tokens." in content


def test_user_help_tool_agent_instructions_direct_concise_answers_and_doc_citations(tmp_path: Path) -> None:
    tool = make_service(tmp_path)
    instructions = tool._create_agent().instruction
    assert "simple, answer it concisely" in instructions
    assert "general or broad" in instructions
    assert "refer the user to the relevant documentation guide" in instructions
    assert "Always cite links to the relevant documentation for any answer" in instructions
    assert "/docs/virtual-tabletop" in instructions
    assert "/docs/theater-yaml" in instructions


def test_feedback_guide_is_discoverable_readable_and_citable(tmp_path: Path) -> None:
    tool = make_service(tmp_path)
    tool.help_roots = tool._default_help_roots()
    assert "docs/theater_feedback_and_reports.md (doc URL: /docs/feedback-and-reporting)" in tool.list_help_files()
    assert "theater_feedback_and_reports.md" in tool.search_help_files("Feedback/Report Theater")
    assert "Send Report" in tool.read_help_file("/docs/feedback-and-reporting")
    assert "/docs/feedback-and-reporting" in tool._create_agent().instruction
    assert normalize_doc_links("[Feedback](docs/theater_feedback_and_reports.md)") == "[Feedback](/docs/feedback-and-reporting)"
    for question in ("How do I report a malicious theater?", "How do I file a bug?", "Where can I suggest an improvement?"):
        assert "[Feedback and Reporting](/docs/feedback-and-reporting)" in ensure_doc_citation("Open the left-hand menu.", question)

