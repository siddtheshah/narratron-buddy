from pathlib import Path
from types import SimpleNamespace

from components.canvas.canvas_state_service import CanvasStateService


class FakeTheater:
    def __init__(self, theater_id: str, root: Path) -> None:
        self.theater_id, self.root = theater_id, root / theater_id

    def directory(self) -> Path: return self.root
    def output_dir(self) -> Path: return self.root / "output"
    def get_url_for_path(self, path: str) -> str: return path


class FakeTheaterManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.deployed = [SimpleNamespace(theater_id="live", status="deployed")]

    def list_theaters(self): return self.deployed
    def theater(self, theater_id: str) -> FakeTheater: return FakeTheater(theater_id, self.root)


def test_service_caches_one_manager_per_theater_and_selects_deployed_default(tmp_path: Path) -> None:
    service = CanvasStateService(FakeTheaterManager(tmp_path))

    default = service.get()
    explicit = service.get("draft")

    assert default.theater_id == "live"
    assert service.get("live") is default
    assert service.get("draft") is explicit
    assert set(service.states) == {"live", "draft"}


def test_service_fallback_when_no_deployed_theater(tmp_path: Path) -> None:
    class EmptyTheaterManager:
        def __init__(self, root: Path) -> None:
            self.root = root

        def list_theaters(self):
            return []

        def theater(self, theater_id: str) -> FakeTheater:
            return FakeTheater(theater_id, self.root)

    service = CanvasStateService(EmptyTheaterManager(tmp_path))

    # With no deployed theaters and no cached states, defaults to "default"
    default_mgr = service.get()
    assert default_mgr.theater_id == "default"

    # When another theater is cached, fallback prefers the non-default cached theater
    custom_mgr = service.get("my_adventure")
    assert service.get().theater_id == "my_adventure"

