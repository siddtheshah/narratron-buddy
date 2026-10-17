"""Root test configuration and fixtures."""

from collections.abc import AsyncIterator, Generator
from typing import TypedDict
from unittest.mock import MagicMock, patch
import pytest
import pytest_socket

# pytest-socket normally starts guarding at test setup. Guard application imports
# too, so collection cannot accidentally contact live services.
pytest_socket.disable_socket(allow_unix_socket=True)

from google.adk.agents.run_config import RunConfig  # noqa: E402
from google.genai import types  # noqa: E402

import object_registry  # noqa: E402


class TextSpanDict(TypedDict):
    text: str
    effect: str
    font: str
    color: str | None


class DialogueItemDict(TypedDict, total=False):
    speaker: str
    text: str
    kind: str
    voice_instruction: str
    spans: list[TextSpanDict]


class BeautifiedSceneDict(TypedDict):
    narration: str
    narration_spans: list[TextSpanDict]
    dialogue: list[DialogueItemDict]


def _mock_beautify_scene(
    narration: str, dialogue: list[dict[str, str]]
) -> BeautifiedSceneDict:
    clean_narration = str(narration or "").strip()
    spans: list[TextSpanDict] = (
        [{"text": clean_narration, "effect": "none", "font": "default", "color": None}]
        if clean_narration
        else []
    )
    formatted_dialogue: list[DialogueItemDict] = [
        {
            **item,
            "spans": [
                {
                    "text": str(item.get("text") or ""),
                    "effect": "none",
                    "font": "default",
                    "color": None,
                }
            ],
        }
        for item in (dialogue or [])
    ]
    return {
        "narration": narration,
        "narration_spans": spans,
        "dialogue": formatted_dialogue,
    }


def _mock_beautify_text(text: str) -> list[TextSpanDict]:
    clean_text = str(text or "").strip()
    return (
        [{"text": clean_text, "effect": "none", "font": "default", "color": None}]
        if clean_text
        else []
    )


@pytest.fixture(autouse=True)
def mock_text_beautifier() -> Generator[None, None, None]:
    """Prevent live model calls by mocking text_beautifier on object_registry during tests."""
    with (
        patch.object(
            object_registry.text_beautifier,
            "beautify_scene",
            side_effect=_mock_beautify_scene,
        ),
        patch.object(
            object_registry.text_beautifier,
            "beautify_text",
            side_effect=_mock_beautify_text,
        ),
    ):
        yield


async def _empty_runner_run_async(
    user_id: str = "",
    session_id: str = "",
    new_message: types.Content | None = None,
    run_config: RunConfig | None = None,
) -> AsyncIterator[MagicMock]:
    if False:
        yield MagicMock()


def _create_mock_deep_planner_runner() -> MagicMock:
    runner = MagicMock()
    runner.run_async = _empty_runner_run_async
    return runner


@pytest.fixture(autouse=True)
def mock_story_deep_planner_runner() -> Generator[None, None, None]:
    """Prevent unmocked deep planning runner from making live Vertex AI requests during tests."""
    with patch(
        "tools.story.story_planning_module.StoryPlanningModule._get_or_create_deep_planner_runner",
        side_effect=_create_mock_deep_planner_runner,
    ):
        yield

