from pathlib import Path
from types import SimpleNamespace

from components.canvas.canvas_state_service import CanvasStateService


class FakeTheater:
    def __init__(self, theater_id: str, root: Path) -> None:
        self.theater_id, self.root = theater_id, root / theater_id

    def directory(self) -> Path: return self.root
    def output_dir(self) -> Path: return self.root / "output"
    def chats_dir(self) -> Path: return self.root / "chats"
    def image_artifacts_dir(self) -> Path: return self.root / "images"
    def references_dir(self) -> Path: return self.root / "references"
    def config(self) -> dict: return {}
    def get_url_for_path(self, path: str) -> str: return path


class FakeTheaterManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.deployed = [SimpleNamespace(theater_id="live", status="deployed")]

    def list_theaters(self): return self.deployed
    def theater(self, theater_id: str) -> FakeTheater: return FakeTheater(theater_id, self.root)


import pytest


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

