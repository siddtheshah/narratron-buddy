#!/usr/bin/env python3
"""Run live, source-grounded smoke questions against ``user_help_tool``.

Run from the project root:
    uv run python -m scripts.user_help_tool_smoke

The script uses the configured ADK Gemini model and therefore makes live model
requests. Supply ``--question`` more than once to replace the built-in Adventure
Mode questions.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from dotenv import load_dotenv

from components.canvas_state import CanvasStateManager
from components.theater_manager import TheaterManager
from services.user_help_service import DEFAULT_USER_HELP_MODEL
from tools.user_help_tool import UserHelpTool

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_QUESTIONS = (
    "How do I turn on Adventure Mode for an existing theater?",
    "Where is the theater configuration editor, and which setting enables Adventure Mode?",
    "After I enable Adventure Mode for an existing theater, how do I save the change and begin the adventure?",
    "What controls and shortcuts should an orator use when running an Adventure Mode session?",
)


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ask live UI-help questions using the current local help files.")
    parser.add_argument(
        "--question",
        action="append",
        dest="questions",
        help="A question to ask. Repeat to ask multiple questions; overrides the built-in Adventure Mode checks.",
    )
    parser.add_argument("--model", default=DEFAULT_USER_HELP_MODEL, help="ADK model used by the help agent.")
    parser.add_argument("--theater-id", default="default", help="Theater whose cooldown and configuration apply.")
    return parser.parse_args()


def main() -> None:
    """Load project credentials, run the selected questions, and print each answer."""
    load_dotenv(_PROJECT_ROOT / ".env")
    arguments = _parse_arguments()
    questions: tuple[str, ...] = tuple(arguments.questions) if arguments.questions else _DEFAULT_QUESTIONS
    theater = TheaterManager().theater(arguments.theater_id)
    help_tool = UserHelpTool(
        theater=theater,
        canvas_manager=CanvasStateManager(theater),
        model=arguments.model,
    )

    print(f"Running {len(questions)} live user_help_tool question(s) with ADK model {arguments.model}.")
    for number, question in enumerate(questions, start=1):
        print(f"\n[{number}] Question: {question}")
        status = asyncio.run(help_tool.user_help_tool(question))
        print(f"Result: {status}")
        chat = help_tool.canvas_manager.chat
        if chat and chat.messages:
            last_message = chat.messages[-1]
            print("\n--- Answer Text ---")
            print(last_message.get("text", ""))
            print("\n--- Rendered HTML ---")
            print(last_message.get("html", ""))


if __name__ == "__main__":
    main()
