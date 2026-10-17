"""Root test configuration and fixtures."""

from typing import Generator, TypedDict
from unittest.mock import patch
import pytest

import object_registry


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
