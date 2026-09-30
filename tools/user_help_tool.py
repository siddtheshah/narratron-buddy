"""An ADK file-browsing agent for Narratron user-interface help."""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Lock
from uuid import uuid4

from google.adk.agents import Agent
from google.adk.apps.app import App
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types


_TEXT_FILE_SUFFIXES = frozenset({".html", ".md"})
_MAX_SEARCH_CALLS = 3
_MAX_READ_CALLS = 3
_MAX_READ_LINES = 240


class UserHelpTool:
    """Answer UI questions through a bounded ADK agent that browses help files."""

    def __init__(
        self,
        model: str,
        help_roots: tuple[Path, ...] | None = None,
        session_service: InMemorySessionService | None = None,
        max_output_tokens: int = 1_200,
    ) -> None:
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

    def user_help_tool(self, question: str) -> str:
        """Research current templates and docs, then answer a UI usage question.

        Args:
            question: The user's question about controls, workflows, roles, or keyboard shortcuts.

        Returns:
            Detailed, source-grounded instructions suitable for posting directly in chat.
        """
        clean_question = question.strip()
        if not clean_question:
            return "Please ask a specific question about using the Narratron interface."
        self._reset_call_counts()
        return self._run_agent(clean_question)

    def list_help_files(self) -> str:
        """List the current template and documentation files available for UI-help research."""
        files = [self._display_path(path) for path in self._text_files()]
        return "\n".join(files) if files else "No help files are available."

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
                "the current local templates and documentation available through your tools. First search for "
                "focused phrases, visible UI labels, or exact configuration keys; avoid generic searches such as "
                "'mode' or 'settings'. Read the relevant line ranges before answering. You have at most three "
                "searches and three reads, so use them deliberately. Never invent a UI control, shortcut, or "
                "workflow. If the files do not establish an answer, say that plainly. Give practical numbered "
                "steps and name controls, keys, and shortcuts exactly as the source confirms them. Do not mention "
                "tools, source files, or internal implementation details in the final answer."
            ),
            tools=[self.list_help_files, self.search_help_files, self.read_help_file],
            generate_content_config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=self.max_output_tokens,
            ),
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
        )

    def _run_agent(self, question: str) -> str:
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
            answer = asyncio.run(asyncio.wait_for(run_turn(), timeout=45.0))
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
        for path in self._text_files():
            if self._display_path(path) == requested_path:
                return path
        return None


__all__ = ["UserHelpTool"]
