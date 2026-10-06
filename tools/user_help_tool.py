"""An ADK file-browsing agent for Narratron user-interface help."""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
from threading import Lock
from uuid import uuid4

from google.adk.agents import Agent
from google.adk.apps.app import App
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater
from tools.base_tool import BaseTools, with_cycle_cooldown
from tools.tool_metadata import terminal
from utils.markdown import render_markdown


_TEXT_FILE_SUFFIXES = frozenset({".html", ".md"})
_MAX_SEARCH_CALLS = 3
_MAX_READ_CALLS = 3
_MAX_READ_LINES = 240
_HELP_CHAT_AUTHOR = "Narratron User Help"

_DOC_URL_BY_FILE: dict[str, str] = {
    "virtual_tabletop_guide.md": "/docs/virtual-tabletop",
    "beyond20.md": "/docs/beyond20",
    "writing_adventures.md": "/docs/writing-adventures",
    "theater_yaml_docs.html": "/docs/theater-yaml",
    "ideas.html": "/docs/ideas",
    "about.html": "/docs/about",
    "docs.html": "/docs",
    "terms_of_service.md": "/docs/terms",
    "privacy_policy.md": "/docs/privacy",
}

_DOC_FILE_BY_URL: dict[str, str] = {
    "/docs/virtual-tabletop": "virtual_tabletop_guide.md",
    "/docs/virtual_tabletop": "virtual_tabletop_guide.md",
    "/docs/vtt": "virtual_tabletop_guide.md",
    "/docs/beyond20": "beyond20.md",
    "/docs/writing-adventures": "writing_adventures.md",
    "/docs/theater-yaml": "theater_yaml_docs.html",
    "/docs/ideas": "ideas.html",
    "/docs/about": "about.html",
    "/docs": "docs.html",
    "/docs/terms": "terms_of_service.md",
    "/docs/privacy": "privacy_policy.md",
}


def normalize_doc_links(answer: str) -> str:
    """Ensure documentation links use public Narratron web URLs."""
    def replace_link(match: re.Match[str]) -> str:
        label = match.group(1)
        target = match.group(2).strip()
        if target.startswith("/docs/virtual_tabletop"):
            target = target.replace("/docs/virtual_tabletop", "/docs/virtual-tabletop", 1)
        target_name = Path(target.split("#")[0]).name
        anchor = f"#{target.split('#')[1]}" if "#" in target else ""
        if target_name in _DOC_URL_BY_FILE:
            return f"[{label}]({_DOC_URL_BY_FILE[target_name]}{anchor})"
        return f"[{label}]({target})"

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace_link, answer)


def ensure_doc_citation(answer: str, question: str) -> str:
    """Ensure the answer always contains a citation link to Narratron documentation."""
    if re.search(r"\[[^\]]+\]\((?:/docs[^\)]*|https?://[^\)]*)\)", answer):
        return answer
    lower = f"{question} {answer}".casefold()
    if any(k in lower for k in ["battlemap", "action wheel", "action-wheel", "wheel", "vtt", "tabletop", "stamp", "mini", "token"]):
        fallback_link = "[Virtual Tabletop Guide](/docs/virtual-tabletop)"
    elif any(k in lower for k in ["theater.yaml", "config", "setting", "yaml", "live_agent", "persona"]):
        fallback_link = "[theater.yaml Reference](/docs/theater-yaml)"
    elif any(k in lower for k in ["beyond20", "dice", "roll", "d&d beyond"]):
        fallback_link = "[Beyond20 Integration Guide](/docs/beyond20)"
    elif any(k in lower for k in ["adventure", "lore", "story beat", "scene", "writing"]):
        fallback_link = "[Writing Adventures Guide](/docs/writing-adventures)"
    elif any(k in lower for k in ["idea", "recipe", "effect"]):
        fallback_link = "[Ideas & Recipes](/docs/ideas)"
    elif any(k in lower for k in ["terms", "policy", "privacy"]):
        fallback_link = "[Terms of Service](/docs/terms)"
    else:
        fallback_link = "[Narratron Documentation](/docs)"
    return f"{answer.rstrip()}\n\nFor more details, see the {fallback_link}."


class UserHelpTool(BaseTools):
    """Answer UI questions through a bounded ADK agent that browses help files."""

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
        self.model = model
        self.help_roots = help_roots or self._default_help_roots()
        self.session_service = session_service or InMemorySessionService()
        self.max_output_tokens = max_output_tokens
        self._search_calls = 0
        self._read_calls = 0
        self._call_lock = Lock()
        self._agent = self._create_agent()
        self._app = App(name="narratron_user_help", root_agent=self._agent)
        self._runner = Runner(app=self._app, session_service=self.session_service, auto_create_session=True)

    @staticmethod
    def _default_help_roots() -> tuple[Path, ...]:
        project_root = Path(__file__).resolve().parent.parent
        return (project_root / "templates", project_root / "docs")

    @terminal
    @with_cycle_cooldown(action_desc="researching another interface-help question")
    async def user_help_tool(self, question: str) -> str:
        """Research current templates and docs, then answer a UI usage question.

        Args:
            question: The user's question about controls, workflows, roles, or keyboard shortcuts.

        Returns:
            A brief status after the detailed answer is posted directly to chat.
        """
        clean_question = question.strip()
        if not clean_question:
            return "Please ask a specific question about using the Narratron interface."
        self._reset_call_counts()
        raw_answer = await self._run_agent(clean_question)
        normalized_answer = normalize_doc_links(raw_answer)
        final_answer = ensure_doc_citation(normalized_answer, clean_question)
        self._send_help_chat_message(final_answer)
        return "User help answer posted in chat."

    def _send_help_chat_message(self, answer: str) -> None:
        """Publish the completed answer to the shared canvas chat stream."""
        chat = self.canvas_manager.chat
        if chat is None:
            return
        chat.add_message({
            "author": _HELP_CHAT_AUTHOR,
            "text": answer,
            "html": render_markdown(answer, open_in_new_tab=True),
            "type": "user_help",
        })
        self.canvas_manager.notify_changed("chat")

    def list_help_files(self) -> str:
        """List the current template and documentation files available for UI-help research with their doc URLs."""
        lines: list[str] = []
        for path in self._text_files():
            disp = self._display_path(path)
            doc_url = _DOC_URL_BY_FILE.get(path.name)
            if doc_url:
                lines.append(f"{disp} (doc URL: {doc_url})")
            else:
                lines.append(disp)
        return "\n".join(lines) if lines else "No help files are available."

    def search_help_files(self, query: str) -> str:
        """Search current templates and docs for a focused phrase, UI label, or configuration key."""
        with self._call_lock:
            if self._search_calls >= _MAX_SEARCH_CALLS:
                return "Search limit reached. Read the most relevant result and answer now."
            self._search_calls += 1

        clean_query = query.strip().casefold()
        if not clean_query:
            return "Provide a focused search phrase."
        matches: list[tuple[Path, int, str]] = []
        for path in self._text_files():
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line_number, line in enumerate(lines, start=1):
                if clean_query in line.casefold():
                    matches.append((path, line_number, line.strip()))
        if not matches:
            return f"No matches for {query!r}. Try an exact label or configuration key."
        results = [
            f"{self._display_path(path)}:{line_number}: {line[:300]}"
            for path, line_number, line in matches[:20]
        ]
        return "\n".join(results)

    def read_help_file(self, path: str, start_line: int = 1, max_lines: int = 120) -> str:
        """Read a bounded line range from a path returned by list_help_files or search_help_files."""
        with self._call_lock:
            if self._read_calls >= _MAX_READ_CALLS:
                return "Read limit reached. Answer using the passages already gathered."
            self._read_calls += 1

        target = self._resolve_help_path(path)
        if target is None:
            return "That path is not in the allowed templates or docs help corpus."
        safe_start_line = max(1, start_line)
        safe_max_lines = min(max(1, max_lines), _MAX_READ_LINES)
        try:
            lines = target.read_text(encoding="utf-8").splitlines()
        except OSError:
            return f"Could not read {path}."
        start_index = safe_start_line - 1
        if start_index >= len(lines):
            return f"{path} has only {len(lines)} lines."
        end_index = min(len(lines), start_index + safe_max_lines)
        numbered_lines = [
            f"{line_number}: {line}"
            for line_number, line in enumerate(lines[start_index:end_index], start=safe_start_line)
        ]
        return f"{self._display_path(target)} lines {safe_start_line}-{end_index}:\n" + "\n".join(numbered_lines)

    def _create_agent(self) -> Agent:
        return Agent(
            name="narratron_user_help_agent",
            description="Answers Narratron UI questions by browsing current templates and documentation.",
            model=self.model,
            instruction=(
                "You are Narratron Buddy's UI-help specialist. Answer the user's question accurately using only "
                "the current local templates and documentation available through your tools.\n\n"
                "- If the question is simple, answer it concisely with direct steps or explanation.\n"
                "- If the inquiry area is general or broad, provide a concise summary and refer the user to the relevant documentation guide.\n"
                "- Always cite links to the relevant documentation for any answer using Markdown links (e.g. [Guide Name](/docs/...)). "
                "Every answer must include at least one documentation citation link.\n\n"
                "Canonical documentation guides available:\n"
                "- [Virtual Tabletop Guide](/docs/virtual-tabletop): 2D battlemaps, Orator Action Wheel, stamps, tokens & minis, tactical movement, and Beyond20 rolls.\n"
                "- [theater.yaml Reference](/docs/theater-yaml): Configuration reference covering all sections, settings, live_agent, canvas, visuals, and audio.\n"
                "- [Writing Adventures Guide](/docs/writing-adventures): Authoring interactive adventures, world lore, and scene design.\n"
                "- [Beyond20 Integration Guide](/docs/beyond20): Beyond20 extension setup and forwarding D&D Beyond rolls.\n"
                "- [Ideas & Recipes](/docs/ideas): Customization recipes, prompt techniques, and image effects.\n"
                "- [About Narratron](/docs/about): Platform overview and background.\n"
                "- [Documentation Home](/docs): General documentation index.\n\n"
                "First search for focused phrases, visible UI labels, or exact configuration keys; avoid generic searches such as "
                "'mode' or 'settings'. Read the relevant line ranges before answering. You have at most three "
                "searches and three reads, so use them deliberately. Never invent a UI control, shortcut, or "
                "workflow. If the files do not establish an answer, say that plainly. Format your answer as Markdown: "
                "use short headings when helpful, blank lines between paragraphs and lists, bold for control names, "
                "and inline code for keys and shortcuts. Do not wrap the whole answer in a code fence or use raw HTML. "
                "Give practical numbered steps and name controls, keys, and shortcuts exactly as the source confirms them. "
                "Always cite documentation using relative web paths (/docs/...), never internal file paths (like docs/*.md or templates/*.html). "
                "Do not mention tools, source file paths, or internal implementation details in the final answer."
            ),
            tools=[self.list_help_files, self.search_help_files, self.read_help_file],
            generate_content_config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=self.max_output_tokens,
            ),
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
        )

    async def _run_agent(self, question: str) -> str:
        """Run one isolated ADK help turn without blocking a tool worker thread."""
        async def run_turn() -> str:
            answer = ""
            async for event in self._runner.run_async(
                user_id="user_help",
                session_id=uuid4().hex,
                new_message=types.Content(role="user", parts=[types.Part(text=question)]),
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    answer = "".join(part.text or "" for part in event.content.parts).strip()
            return answer
        try:
            answer = await asyncio.wait_for(run_turn(), timeout=45.0)
        except (asyncio.TimeoutError, TimeoutError):
            return "I couldn't finish researching the interface in time. Please try again."
        except Exception:
            return "I couldn't prepare interface help right now. Please try again in a moment."
        return answer or "I couldn't find a usable answer in the current interface guide."

    def _reset_call_counts(self) -> None:
        with self._call_lock:
            self._search_calls = 0
            self._read_calls = 0

    def _text_files(self) -> list[Path]:
        files: list[Path] = []
        for root in self.help_roots:
            if root.exists():
                files.extend(
                    path for path in root.rglob("*")
                    if path.is_file() and path.suffix.casefold() in _TEXT_FILE_SUFFIXES
                )
        return sorted(files)

    def _display_path(self, path: Path) -> str:
        for root in self.help_roots:
            try:
                return str(path.relative_to(root.parent)).replace("\\", "/")
            except ValueError:
                continue
        return path.name

    def _resolve_help_path(self, requested_path: str) -> Path | None:
        clean_path = requested_path.split(" (")[0].strip()
        mapped_file = _DOC_FILE_BY_URL.get(clean_path)
        for path in self._text_files():
            if mapped_file and path.name == mapped_file:
                return path
            disp = self._display_path(path)
            if disp == requested_path or disp == clean_path or path.name == clean_path:
                return path
        return None


__all__ = ["UserHelpTool"]
