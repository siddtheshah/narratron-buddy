"""Root test configuration and fixtures."""

from collections.abc import AsyncIterator, Generator
import os
import threading
from typing import TypedDict
from unittest.mock import MagicMock, patch
import pytest
import pytest_socket
from absl.testing import flagsaver
from testing.network import install_local_socketpair

# pytest-socket normally starts guarding at test setup. Guard application imports
# too, so collection cannot accidentally contact live services.
pytest_socket.disable_socket(allow_unix_socket=True)
install_local_socketpair()

from google.adk.agents.run_config import RunConfig  # noqa: E402
from google.genai import types  # noqa: E402

from testing.bootstrap import initialize_test_registry  # noqa: E402

initialize_test_registry()

import object_registry  # noqa: E402
from providers.text_response_provider import TextResponseProvider  # noqa: E402
from pydantic import JsonValue  # noqa: E402
from tools.story.story_planning_module import StoryPlanningModule  # noqa: E402


@pytest.fixture(autouse=True)
def restore_test_environment() -> Generator[None, None, None]:
    """Restore environment and runtime flag mutations even when a test fails."""
    with patch.dict(os.environ), flagsaver.flagsaver():
        yield


@pytest.fixture(autouse=True)
def isolate_registry_storage(tmp_path_factory: pytest.TempPathFactory) -> Generator[None, None, None]:
    """Give shared services fresh storage and canvas state for every test."""
    tmp_path = tmp_path_factory.mktemp("registry")
    database = object_registry.db
    canvas = object_registry.canvas_states
    database.close()
    canvas.states.clear()
    for name in ("ephemeral", "theaters", "stamps", "adventures", "music_catalog"):
        (tmp_path / name).mkdir()
    with (
        patch("components.theater_manager.ensure_ephemeral_root", return_value=tmp_path / "ephemeral"),
        patch("storage.theater_repository.ensure_theaters_root", return_value=tmp_path / "theaters"),
        patch("storage.stamp_storage.ensure_stamps_root", return_value=tmp_path / "stamps"),
        patch("services.adventure_service.ensure_adventures_root", return_value=tmp_path / "adventures"),
        patch("services.music_catalog.get_music_catalog_root", return_value=tmp_path / "music_catalog"),
        patch.object(database, "db_path", str(tmp_path / "registry.db")),
        patch.object(object_registry.theater_manager, "base_dir", tmp_path / "ephemeral"),
        patch.object(object_registry.theater_repository, "_base_dir", tmp_path / "theaters"),
        patch.object(object_registry.stamp_storage, "_base_dir", tmp_path / "stamps"),
        patch.object(object_registry.adventure_service, "_base_dir", tmp_path / "adventures"),
        patch.object(object_registry.music_catalog, "directory", tmp_path / "music_catalog"),
    ):
        try:
            yield
        finally:
            canvas.states.clear()
            database.close()


@pytest.fixture
def offline_lab_text_providers() -> Generator[MagicMock, None, None]:
    """Mock Test Lab construction; individual tests supply generation results."""
    provider = MagicMock(spec=TextResponseProvider)
    provider.generate.side_effect = AssertionError("This test must mock text generation")
    with (
        patch("testlab.adventure_runner.get_text_response_provider", return_value=provider),
        patch("testlab.server.get_text_response_provider", return_value=provider),
        patch("services.text_beautifier.get_text_response_provider", return_value=provider),
    ):
        yield provider


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
    workers: set[threading.Thread] = set()
    queue_planning = StoryPlanningModule.queue_deep_planning

    def track_planning(
        module: StoryPlanningModule, turn_id: int, action: str, result: dict[str, JsonValue],
    ) -> threading.Thread:
        worker = queue_planning(module, turn_id, action, result)
        workers.add(worker)
        return worker

    with (
        patch(
            "tools.story.story_planning_module.StoryPlanningModule._get_or_create_deep_planner_runner",
            side_effect=_create_mock_deep_planner_runner,
        ),
        patch.object(StoryPlanningModule, "queue_deep_planning", track_planning),
    ):
        try:
            yield
        finally:
            for worker in workers:
                worker.join(timeout=5.0)
                assert not worker.is_alive(), "Deep planner worker outlived its test"

