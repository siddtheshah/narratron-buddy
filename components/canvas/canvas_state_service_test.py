from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from components.canvas.canvas_state_service import CanvasStateService


class FakeTheater:
    def __init__(self, theater_id: str, root: Path) -> None:
        self.theater_id, self.root = theater_id, root / theater_id
        self.storage_change_count = 0

    def storage_changed(self) -> None:
        self.storage_change_count += 1

    def directory(self) -> Path:
        return self.root

    def output_dir(self) -> Path:
        return self.root / "output"

    def chats_dir(self) -> Path:
        return self.root / "chats"

    def image_artifacts_dir(self) -> Path:
        return self.root / "images"

    def references_dir(self) -> Path:
        return self.root / "references"

    def stamps_dir(self) -> Path:
        return self.root / "stamps"

    def config(self) -> dict[str, object]:
        return {}

    def get_url_for_path(self, path: str) -> str:
        return path


class FakeTheaterManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.deployed = [SimpleNamespace(theater_id="live", status="deployed")]

    def list_theaters(self) -> list[SimpleNamespace]:
        return self.deployed

    def theater(self, theater_id: str) -> FakeTheater:
        return FakeTheater(theater_id, self.root)


def test_service_caches_one_manager_per_theater(tmp_path: Path) -> None:
    service = CanvasStateService(FakeTheaterManager(tmp_path))

    live = service.get("live")
    draft = service.get("draft")

    assert live.theater_id == "live"
    assert service.get("live") is live
    assert service.get("draft") is draft
    assert set(service.states) == {"live", "draft"}


def test_service_requires_theater_id(tmp_path: Path) -> None:
    service = CanvasStateService(FakeTheaterManager(tmp_path))

    with pytest.raises(ValueError) as excinfo:
        service.get("")
    assert "theater_id is required" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        service.get("   ")
    assert "theater_id is required" in str(excinfo.value)


def test_service_keeps_beautifier_after_canvas_reset_and_reload(tmp_path: Path) -> None:
    beautifier = Mock()
    beautifier.beautify_scene.return_value = {"narration_spans": [{"text": "Hello", "effect": "glow"}], "dialogue": []}
    service = CanvasStateService(FakeTheaterManager(tmp_path), text_beautifier=beautifier)
    canvas = service.get("live")

    for refresh in (canvas.reset, canvas.reload_package):
        refresh()
        canvas.story.set_scene("Hello", [])
        assert canvas.story.output_cycle.flush()
        assert canvas.story.narration_spans == [{"text": "Hello", "effect": "glow"}]
    assert beautifier.beautify_scene.call_count == 2


def test_service_preserves_user_stamp_resolver_for_cached_and_history_pages(tmp_path: Path) -> None:
    resolver = Mock(return_value=b"STAMP")
    service = CanvasStateService(FakeTheaterManager(tmp_path), user_stamp_resolver=resolver)
    canvas = service.get("live")
    action = {"type": "stamp", "stamp_id": 42, "url": "/api/stamps/42"}

    for refresh in (canvas.reset, canvas.reload_package):
        refresh()
        assert service.get("live").doodles.resolve_stamp_image(action) == b"STAMP"

    canvas.visual.shown_images_history.append({"page_id": "previous", "doodles": []})
    previous = canvas.doodles_for_page("previous")
    assert previous is not None
    assert previous.resolve_stamp_image(action) == b"STAMP"
    assert resolver.call_count == 3
