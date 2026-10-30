"""Integration coverage for authorization, imports, billing, and publishing drafts."""

import asyncio
import json
from io import BytesIO
import zipfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier, Event
from typing import TypedDict
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic import JsonValue
from PIL import Image
import pytest

import object_registry
from api_server.shared import app
from components.canvas.canvas_state_service import CanvasStateService
from components.canvas.visual_state import VisualState
from components.theater_manager import TheaterManager
from pricing.pricing_controller import PricingController
from testing.reference_images import png_bytes
from providers.image_provider import ImageGenerationResult
from providers.music_provider import MusicGenerationResult
from services.google_asset_importer import GoogleImportResult
from services.theater_builder import BuilderProposal, ChatMessage, DraftInfo, GenerationRequest, TheaterBuilderStore
from services.live_agent import get_playlists_context
from services.music_catalog import MusicCatalog
from storage.theater_repository import TheaterRepository
from tools.music_tool import MusicTools


class DraftInfoPayload(TypedDict):
    theater_id: str
    owner_id: int
    name: str
    revision: int
    saved_revision: int


class FilePayload(TypedDict):
    path: str
    size: int
    kind: str


class DraftPayload(TypedDict):
    draft: DraftInfoPayload
    files: list[FilePayload]
    rates: dict[str, float]


@dataclass
class BuilderHarness:
    client: TestClient
    manager: TheaterManager
    repository: TheaterRepository
    database: MagicMock

    def create(self, *, default: bool = False) -> DraftPayload:
        result = self.client.post("/api/theater-editor/drafts", json={"name": "Harbor", "populate_default": default})
        assert result.status_code == 200, result.text
        return result.json()


@pytest.fixture
def builder(tmp_path: Path) -> Iterator[BuilderHarness]:
    manager = TheaterManager(tmp_path / "runtime")
    repository = TheaterRepository(tmp_path / "repository")
    database = MagicMock()
    deployments: dict[str, dict[str, JsonValue]] = {}
    database.get_deployment.return_value = None

    def get_deployment(theater_id: str) -> dict[str, JsonValue] | None:
        return database.get_deployment.return_value or deployments.get(theater_id)

    database.get_deployment.side_effect = get_deployment

    def record_deployment(theater_id: str, user_id: int, join_key: str, *, cost: float, name: str) -> bool:
        deployments[theater_id] = {"user_id": user_id, "join_key": join_key, "name": name}
        return True

    database.record_deployment.side_effect = record_deployment
    database.get_user_by_id.return_value = {"id": 7, "credits": 30.0}
    database.record_user_usage.return_value = {"credits": 26.0}
    pricing = PricingController(image_credit_rate=4.0, music_credit_rate=6.0)

    async def authenticate(request: Request) -> dict[str, int] | None:
        identifier = request.headers.get("x-test-user")
        return {"id": int(identifier)} if identifier else None

    with patch.object(object_registry, "db", database), patch.object(object_registry, "theater_manager", manager), patch.object(object_registry, "theater_repository", repository), patch.object(object_registry, "pricing_controller", pricing), patch.object(object_registry, "canvas_states", CanvasStateService(manager)), patch.object(object_registry, "live_agent_manager", MagicMock()), patch("api_server.theater_editor.get_current_user_async", new=AsyncMock(side_effect=authenticate)), TestClient(app, headers={"x-test-user": "7"}) as client:
        yield BuilderHarness(client, manager, repository, database)


def test_drafts_require_login_and_owner_for_every_action(builder: BuilderHarness) -> None:
    builder.client.headers.pop("x-test-user")
    assert builder.client.post("/api/theater-editor/drafts", json={}).status_code == 401
    builder.client.headers["x-test-user"] = "7"
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    base = f"/api/theater-editor/{identifier}"
    builder.client.headers["x-test-user"] = "8"
    assert builder.client.get(base).status_code == 403
    assert builder.client.get(f"{base}/file?path=theater.yaml").status_code == 403
    assert builder.client.post(f"{base}/save", json={"revision": 1, "name": "Stolen"}).status_code == 403
    assert builder.client.post(f"{base}/apply", json={"revision": 1, "proposal": {"message": "Change"}}).status_code == 403
    assert builder.client.post(f"{base}/assistant", json={"prompt": "Change"}).status_code == 403
    assert builder.client.post(f"{base}/generate", json={"revision": 1, "kind": "reference", "name": "Hero", "prompt": "Hero"}).status_code == 403
    assert builder.client.post(f"{base}/deploy", json={"revision": 1}).status_code == 403
    assert builder.client.post(f"{base}/upload", data={"revision": 1}, files={"files": ("hero.png", b"image")}).status_code == 403
    assert builder.client.get(f"{base}/download").status_code == 403
    assert builder.client.post(f"{base}/reset", json={"revision": 1}).status_code == 403
    builder.database.record_user_usage.assert_not_called()


@pytest.mark.parametrize(("active_orator_id", "pending"), [(8, False), (8, True), (7, True)])
def test_entering_editor_takes_back_baton_and_cancels_pending_handoffs(builder: BuilderHarness, active_orator_id: int, pending: bool) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    deployment: dict[str, JsonValue] = {
        "user_id": 7, "active_orator_id": active_orator_id,
        "baton_request": '{"target_user_id": 8}' if pending else None,
    }
    builder.database.get_deployment.return_value = deployment
    baton_state = {"active_orator": {"id": 7, "username": "owner"}, "baton_request": None}

    async def take_back(theater_id: str, *, owner_id: int) -> dict[str, JsonValue]:
        deployment["active_orator_id"] = owner_id
        deployment["baton_request"] = None
        return baton_state

    builder.database.take_back_baton_async = AsyncMock(side_effect=take_back)
    with patch("api_server.theater_editor.broadcast_baton_update", new_callable=AsyncMock) as broadcast, \
         patch("api_server.theater_editor.theater_access_cache.invalidate_theater") as invalidate:
        opened = builder.client.get(f"/api/theater-editor/{identifier}")
    assert opened.status_code == 200, opened.text
    builder.database.take_back_baton_async.assert_awaited_once_with(identifier, owner_id=7)
    object_registry.live_agent_manager.set_active_controller.assert_called_once_with(identifier, 7)
    broadcast.assert_awaited_once_with(identifier, baton_state)
    invalidate.assert_called_once_with(identifier)
    assert deployment["active_orator_id"] == 7
    assert deployment["baton_request"] is None


@pytest.mark.parametrize("active_orator_id", [None, 7])
def test_entering_editor_keeps_owner_controller_when_baton_is_already_held(builder: BuilderHarness, active_orator_id: int | None) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    builder.database.get_deployment.return_value = {"user_id": 7, "active_orator_id": active_orator_id}
    opened = builder.client.get(f"/api/theater-editor/{identifier}")
    assert opened.status_code == 200, opened.text
    builder.database.take_back_baton_async.assert_not_called()
    object_registry.live_agent_manager.set_active_controller.assert_not_called()


def test_nonowner_cannot_reclaim_baton_by_opening_editor(builder: BuilderHarness) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    builder.database.get_deployment.return_value = {"user_id": 7, "active_orator_id": 8}
    builder.client.headers["x-test-user"] = "8"
    opened = builder.client.get(f"/api/theater-editor/{identifier}")
    assert opened.status_code == 403
    builder.database.take_back_baton_async.assert_not_called()
    object_registry.live_agent_manager.set_active_controller.assert_not_called()


@pytest.mark.parametrize(("action", "body"), [
    ("save", {"name": "Changed", "writes": [{"path": "lore/new.txt", "content": "New"}], "persist": True}),
    ("apply", {"proposal": {"message": "New lore", "writes": [{"path": "lore/new.txt", "content": "New"}]}}),
    ("assistant", {"prompt": "Develop the harbor"}),
    ("generate", {"kind": "reference", "name": "hero", "prompt": "Hero"}),
    ("delete", {"path": "lore/new.txt"}),
    ("google-link", {"url": "https://drive.google.com/file/d/example/view"}),
    ("deploy", {}),
    ("reset", {}),
])
def test_open_editor_cannot_write_after_owner_passes_baton(builder: BuilderHarness, action: str, body: dict[str, JsonValue]) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    builder.database.get_deployment.return_value = {"user_id": 7, "active_orator_id": 8}
    before = (builder.manager.theater(identifier).directory() / "theater.json").read_bytes()
    result = builder.client.post(f"/api/theater-editor/{identifier}/{action}", json={**body, "revision": data["draft"]["revision"]})
    assert result.status_code == 409, result.text
    assert "Reopen the theater editor" in result.json()["detail"]
    assert (builder.manager.theater(identifier).directory() / "theater.json").read_bytes() == before
    builder.database.take_back_baton_async.assert_not_called()
    builder.database.record_user_usage.assert_not_called()


def test_inflight_generation_is_not_saved_or_charged_after_baton_handoff(builder: BuilderHarness) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]

    def generate(info: DraftInfo, body: GenerationRequest) -> tuple[str, bytes]:
        builder.database.get_deployment.return_value = {"user_id": 7, "active_orator_id": 8}
        return "references/new.webp", b"image"

    with patch("api_server.theater_editor.generate_asset", side_effect=generate):
        result = builder.client.post(f"/api/theater-editor/{identifier}/generate", json={
            "revision": data["draft"]["revision"], "kind": "reference", "name": "hero", "prompt": "Hero",
        })
    assert result.status_code == 409, result.text
    assert not (builder.manager.theater(identifier).directory() / "references/new.webp").exists()
    builder.database.record_user_usage.assert_not_called()
    builder.database.release_generation_slot.assert_called_once()


def test_folder_import_preserves_config_planning_lore_and_playlist(builder: BuilderHarness) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    result = builder.client.post(f"/api/theater-editor/{identifier}/upload", data={"revision": data["draft"]["revision"], "folder": "true"}, files=[
        ("files", ("world/theater.yaml", b"live_agent:\n  special_instructions: Harbor mystery\n")),
        ("files", ("world/planning.yaml", b"Player:\n  initial: Explorer\n")),
        ("files", ("world/references/hero.png", b"image")),
        ("files", ("world/playlists/tension/rain.mp3", b"audio")),
        ("files", ("world/lore/characters/hero.txt", b"Harbor guide")),
    ])
    assert result.status_code == 200, result.text
    paths = {item["path"] for item in result.json()["files"]}
    assert paths == {"theater.yaml", "planning.yaml", "references/hero.png", "playlists/tension/rain.mp3", "lore/characters/hero.txt"}
    assert builder.client.get(f"/api/theater-editor/{identifier}/file?path=theater.yaml").text == "live_agent:\n  special_instructions: Harbor mystery\n"


@pytest.mark.parametrize(("filename", "content", "expected_path"), [
    ("world/theater.yaml", b"live_agent:\n  special_instructions: Imported world\n", "theater.yaml"),
    ("world/lore/guide.md", b"A world guide", "lore/guide.md"),
    ("world/references/harbor.webp", b"image", "references/harbor.webp"),
])
def test_new_folder_import_accepts_partial_packages_and_discards_ineligible_files(builder: BuilderHarness, filename: str, content: bytes, expected_path: str) -> None:
    result = builder.client.post("/api/theater-editor/drafts/import", data={"name": "Imported", "folder": "true"}, files=[
        ("files", (filename, content)),
        ("files", ("world/helper.py", b"ignored")),
        ("files", ("world/references/characters/Loose.webp", b"ignored")),
    ])
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["draft"]["name"] == "Imported"
    assert data["imported_files"] == 1
    assert data["discarded_files"] == ["world/helper.py", "world/references/characters/Loose.webp"]
    assert {item["path"] for item in data["files"]} == {"theater.yaml", expected_path}
    root = TheaterBuilderStore(builder.manager.base_dir).directory(data["draft"]["theater_id"])
    assert (root / expected_path).read_bytes() == content
    if expected_path != "theater.yaml":
        assert (root / "theater.yaml").read_bytes() == b"live_agent: {}\n"
    assert not (root / "helper.py").exists()
    assert not (root / "references/characters/Loose.webp").exists()


@pytest.mark.parametrize("files", [
    [("files", ("world/helper.py", b"ignored"))],
    [("files", ("world/lore/guide.txt", b"guide")), ("files", ("world/theater.yaml", b"live_agent: ["))],
    [("files", ("world/lore/guide.txt", b"guide")), ("files", ("world/../secret.webp", b"image"))],
])
def test_failed_new_import_returns_an_error_without_creating_a_draft(builder: BuilderHarness, files: list[tuple[str, tuple[str, bytes]]]) -> None:
    result = builder.client.post("/api/theater-editor/drafts/import", data={"folder": "true"}, files=files)
    assert result.status_code == 400, result.text
    assert not list(builder.manager.base_dir.glob("*/editor.json"))
    assert not list(builder.manager.base_dir.glob("*/theater.yaml"))


def test_new_import_requires_authentication(builder: BuilderHarness) -> None:
    builder.client.headers.pop("x-test-user")
    result = builder.client.post("/api/theater-editor/drafts/import", files={"files": ("hero.webp", b"image")})
    assert result.status_code == 401


def test_new_import_storage_failure_does_not_leave_a_draft(builder: BuilderHarness) -> None:
    with patch("services.theater_builder.check_user_theaters_storage_allowance", side_effect=ValueError("User theater storage limit exceeded")):
        result = builder.client.post("/api/theater-editor/drafts/import", files={"files": ("hero.webp", b"image")})
    assert result.status_code == 400
    assert "storage limit" in result.json()["detail"]
    assert not list(builder.manager.base_dir.glob("*"))


def test_new_theater_export_failure_does_not_leave_an_unowned_package(builder: BuilderHarness) -> None:
    with patch.object(builder.repository, "export_theater", return_value=False):
        result = builder.client.post("/api/theater-editor/drafts", json={"name": "Harbor", "populate_default": False})
    assert result.status_code == 503
    assert not list(builder.manager.base_dir.iterdir())
    assert not list(builder.repository.base_dir.iterdir())
    builder.database.record_deployment.assert_not_called()


def test_unfinished_adventure_can_be_saved_without_initializing_a_canvas(builder: BuilderHarness) -> None:
    imported = builder.client.post("/api/theater-editor/drafts/import", files={
        "files": ("theater.yaml", b"story_planning:\n  adventure_mode: true\n  enforce_structured: true\n"),
    })
    assert imported.status_code == 200, imported.text
    info = imported.json()["draft"]
    saved = builder.client.post(f"/api/theater-editor/{info['theater_id']}/save", json={
        "revision": info["revision"], "name": info["name"], "persist": True,
    })
    assert saved.status_code == 200, saved.text
    assert info["theater_id"] not in object_registry.canvas_states.states


def test_existing_draft_import_discards_ineligible_files(builder: BuilderHarness) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    base = f"/api/theater-editor/{identifier}"
    result = builder.client.post(f"{base}/upload", data={"folder": "true", "revision": data["draft"]["revision"]}, files=[
        ("files", ("world/lore/guide.txt", b"guide")),
        ("files", ("world/helper.py", b"ignored")),
    ])
    assert result.status_code == 200, result.text
    assert result.json()["discarded_files"] == ["world/helper.py"]
    revision = result.json()["draft"]["revision"]
    bad = builder.client.post(f"{base}/upload", data={"folder": "true", "revision": revision}, files=[
        ("files", ("world/lore/new.txt", b"new")),
        ("files", ("world/theater.yaml", b"live_agent: [")),
    ])
    assert bad.status_code == 400
    assert builder.client.get(base).json()["draft"]["revision"] == revision
    assert builder.client.get(f"{base}/file?path=lore/new.txt").status_code == 404


def test_flat_dump_upload_is_organized_and_invalid_batch_is_rejected(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    result = builder.client.post(f"{base}/upload", data={"revision": data["draft"]["revision"]}, files=[
        ("files", ("hero.png", b"image")), ("files", ("rain.mp3", b"audio")), ("files", ("guide.md", b"Guide")),
    ])
    assert result.status_code == 200, result.text
    paths = {item["path"] for item in result.json()["files"]}
    assert {"references/hero.png", "playlists/default/rain.mp3", "lore/guide.txt"} <= paths
    revision = result.json()["draft"]["revision"]
    bad = builder.client.post(f"{base}/upload", data={"revision": revision}, files=[("files", ("new.png", b"ok")), ("files", ("../bad.png", b"bad"))])
    assert bad.status_code == 400
    assert builder.client.get(f"{base}/file?path=references/new.png").status_code == 404
    duplicate = builder.client.post(f"{base}/upload", data={"revision": revision}, files={"files": ("hero.png", b"replacement")})
    assert duplicate.status_code == 400


def test_upload_assets_rejects_file_over_10mb(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    oversized = b"X" * (10 * 1024 * 1024 + 10)
    result = builder.client.post(
        f"{base}/upload",
        data={"revision": data["draft"]["revision"]},
        files={"files": ("large.png", oversized)},
    )
    assert result.status_code == 400
    assert "at most 10MB" in result.text


def test_example_adventure_folder_survives_editor_upload_and_download(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    source = Path("adventures/example_adventure")
    expected = {path.relative_to(source).as_posix(): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    uploaded = builder.client.post(
        f"{base}/upload", data={"revision": data["draft"]["revision"], "folder": "true"},
        files=[("files", (f"example_adventure/{path}", content)) for path, content in expected.items()],
    )
    assert uploaded.status_code == 200, uploaded.text
    assert {item["path"] for item in uploaded.json()["files"]} == expected.keys()
    downloaded = builder.client.get(f"{base}/download")
    assert downloaded.status_code == 200
    with zipfile.ZipFile(BytesIO(downloaded.content)) as archive:
        assert set(archive.namelist()) == expected.keys()
        for path, content in expected.items():
            assert archive.read(path) == content


def test_reopen_discards_ineligible_source_references(builder: BuilderHarness) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    directory = builder.manager.theater(identifier).directory() / "references/characters"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "Loose.webp").write_bytes(b"image")
    result = builder.client.get(f"/api/theater-editor/{identifier}")
    assert result.status_code == 200, result.text
    assert "references/characters/Loose.webp" not in {item["path"] for item in result.json()["files"]}


def test_upload_assets_rejects_when_user_storage_cap_exceeded(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    with patch("services.theater_builder.check_user_theaters_storage_allowance", side_effect=ValueError("User theater storage limit of 500MB exceeded")):
        result = builder.client.post(
            f"{base}/upload",
            data={"revision": data["draft"]["revision"]},
            files={"files": ("asset.png", b"content")},
        )
    assert result.status_code == 400
    assert "500MB exceeded" in result.text


def test_stale_revision_cannot_overwrite_saved_changes(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    body = {"revision": data["draft"]["revision"], "name": "Harbor", "writes": [{"path": "lore/opening.txt", "content": "Dawn"}]}
    assert builder.client.post(f"{base}/save", json=body).status_code == 200
    assert builder.client.post(f"{base}/save", json=body).status_code == 409
    assert builder.client.post(f"{base}/apply", json={"revision": body["revision"], "proposal": {"message": "Overwrite"}}).status_code == 409


@pytest.mark.parametrize(("kind", "cost", "counter"), [
    ("reference", 4.0, "images_created"),
    ("stamp", 4.0, "images_created"),
    ("playlist", 6.0, "music_created"),
])
def test_generated_assets_use_asset_providers_and_shared_rates(builder: BuilderHarness, kind: str, cost: float, counter: str) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    image = MagicMock()
    image.generate.return_value = ImageGenerationResult(image_bytes=b"image", mime_type="image/png", provider="mock", model="mock")
    music = MagicMock()
    music.generate.return_value = MusicGenerationResult(audio_bytes=b"audio", mime_type="audio/mpeg", provider="mock", model="mock")
    with patch("services.theater_image_generation.get_image_provider", return_value=image), patch("api_server.theater_editor.get_music_provider", return_value=music):
        result = builder.client.post(f"{base}/generate", json={"revision": data["draft"]["revision"], "kind": kind, "name": "harbor", "prompt": "Misty harbor", "playlist": "ambient"})
    assert result.status_code == 200, result.text
    assert result.json()["credits_charged"] == cost
    usage = builder.database.record_user_usage.call_args.kwargs
    assert usage["credit_cost"] == cost
    assert usage[counter] == 1
    assert usage["idempotency_key"].startswith("builder:")
    path = result.json()["path"]
    if kind == "stamp":
        assert path.startswith("stamps/harbor_")
    elif kind == "reference":
        assert path.startswith("references/harbor_")
    else:
        assert path.startswith("playlists/ambient/harbor_")
    assert builder.client.get(f"{base}/file", params={"path": path}).content == (b"image" if kind in ("reference", "stamp") else b"audio")


def test_assistant_proposes_and_generates_stamps(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    proposal = BuilderProposal(
        message="Add battle tokens",
        writes=[],
        moves=[],
        generations=[GenerationRequest(kind="stamp", name="goblin_scout", prompt="Goblin token")],
    )
    builder.database.record_user_usage.return_value = {"credits": 29.9}
    with patch.object(TheaterBuilderStore, "propose", return_value=proposal), patch("api_server.theater_editor.auth_session_cache.invalidate_user"):
        result = builder.client.post(f"{base}/assistant", json={"prompt": "Set up stamps for our encounter"})
    assert result.status_code == 200
    gen = result.json()["proposal"]["generations"][0]
    assert gen["kind"] == "stamp"
    assert gen["name"] == "goblin_scout"

    image = MagicMock()
    image.generate.return_value = ImageGenerationResult(image_bytes=b"goblin-stamp", mime_type="image/png", provider="mock", model="mock")
    with patch("services.theater_image_generation.get_image_provider", return_value=image):
        gen_res = builder.client.post(f"{base}/generate", json={
            "revision": result.json()["revision"],
            "kind": gen["kind"],
            "name": gen["name"],
            "prompt": gen["prompt"],
            "references": gen["references"],
        })
    assert gen_res.status_code == 200
    stamp_path = gen_res.json()["path"]
    assert stamp_path.startswith("stamps/goblin_scout_")
    assert builder.client.get(f"{base}/file", params={"path": stamp_path}).content == b"goblin-stamp"


def test_generated_stamp_preserves_png_alpha_in_draft_and_published_theater(builder: BuilderHarness) -> None:
    draft = builder.create()
    identifier = draft["draft"]["theater_id"]
    base = f"/api/theater-editor/{identifier}"
    buffer = BytesIO()
    image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    image.putpixel((16, 16), (0, 255, 0, 255))
    image.save(buffer, format="PNG")
    provider = MagicMock()
    provider.generate.return_value = ImageGenerationResult(
        image_bytes=buffer.getvalue(), mime_type="image/png", provider="mock", model="mock",
    )
    with patch("services.theater_image_generation.get_image_provider", return_value=provider) as resolve:
        result = builder.client.post(f"{base}/generate", json={
            "revision": draft["draft"]["revision"], "kind": "stamp", "name": "Goblin", "prompt": "Goblin scout",
        })
    assert result.status_code == 200, result.text
    resolve.assert_called_once_with("openai-gpt-image-flare", {"model": "gpt-image-2.5-sunburst", "quality": "medium"})
    request = provider.generate.call_args.args[0]
    assert request.background == "transparent"
    assert request.aspect_ratio == "1:1"
    path = result.json()["path"]
    content = builder.client.get(f"{base}/file", params={"path": path}).content
    with Image.open(BytesIO(content)) as saved:
        assert saved.mode == "RGBA"
        assert saved.getpixel((0, 0)) == (0, 0, 0, 0)
        assert saved.getpixel((16, 16)) == (0, 255, 0, 255)
    published = builder.client.post(f"{base}/deploy", json={"revision": result.json()["state"]["draft"]["revision"]})
    assert published.status_code == 200, published.text
    assert (builder.repository.theater_path(identifier) / path).read_bytes() == buffer.getvalue()


def test_assistant_proposes_and_generates_characters(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    proposal = BuilderProposal(
        message="Add captain character",
        writes=[],
        moves=[],
        generations=[GenerationRequest(kind="character", name="Arthur Modella", prompt="Captain Arthur Modella")],
    )
    builder.database.record_user_usage.return_value = {"credits": 29.9}
    with patch.object(TheaterBuilderStore, "propose", return_value=proposal), patch("api_server.theater_editor.auth_session_cache.invalidate_user"):
        result = builder.client.post(f"{base}/assistant", json={"prompt": "Create our captain character"})
    assert result.status_code == 200
    gen = result.json()["proposal"]["generations"][0]
    assert gen["kind"] == "character"
    assert gen["name"] == "Arthur Modella"

    image = MagicMock()
    image.generate.return_value = ImageGenerationResult(image_bytes=b"arthur-portrait-1", mime_type="image/png", provider="mock", model="mock")
    with patch("services.theater_image_generation.get_image_provider", return_value=image):
        gen_res = builder.client.post(f"{base}/generate", json={
            "revision": result.json()["revision"],
            "kind": gen["kind"],
            "name": gen["name"],
            "prompt": gen["prompt"],
            "references": gen["references"],
        })
    assert gen_res.status_code == 200
    char_path1 = gen_res.json()["path"]
    assert char_path1 == "references/characters/Arthur Modella/1.png"
    assert builder.client.get(f"{base}/file", params={"path": char_path1}).content == b"arthur-portrait-1"

    # Second generation for the same character increments iteration to 2.png
    image.generate.return_value = ImageGenerationResult(image_bytes=b"arthur-portrait-2", mime_type="image/png", provider="mock", model="mock")
    with patch("services.theater_image_generation.get_image_provider", return_value=image):
        gen_res2 = builder.client.post(f"{base}/generate", json={
            "revision": gen_res.json()["state"]["draft"]["revision"],
            "kind": gen["kind"],
            "name": gen["name"],
            "prompt": "Evolved portrait",
            "references": [char_path1],
        })
    assert gen_res2.status_code == 200
    char_path2 = gen_res2.json()["path"]
    assert char_path2 == "references/characters/Arthur Modella/2.png"
    assert builder.client.get(f"{base}/file", params={"path": char_path2}).content == b"arthur-portrait-2"

    # Verify deployment publishes characters to the live theater
    published = builder.client.post(f"{base}/deploy", json={"revision": gen_res2.json()["state"]["draft"]["revision"]})
    assert published.status_code == 200
    identifier = data["draft"]["theater_id"]
    assert (builder.repository.theater_path(identifier) / char_path1).read_bytes() == b"arthur-portrait-1"
    assert (builder.repository.theater_path(identifier) / char_path2).read_bytes() == b"arthur-portrait-2"


def test_scene_generation_and_evolution_stores_in_scenes_folder_and_increments_iteration(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    builder.database.record_user_usage.return_value = {"credits": 29.9}

    proposal = BuilderProposal(
        message="Created Old Harbor scene",
        generations=[GenerationRequest(kind="scene", name="Old Harbor", prompt="A misty harbor at dawn")]
    )
    with patch.object(TheaterBuilderStore, "propose", return_value=proposal), patch("api_server.theater_editor.auth_session_cache.invalidate_user"):
        result = builder.client.post(f"{base}/assistant", json={"prompt": "Generate a scene visual"})
    assert result.status_code == 200
    gen = result.json()["proposal"]["generations"][0]
    assert gen["kind"] == "scene"
    assert gen["name"] == "Old Harbor"

    image = MagicMock()
    image.generate.return_value = ImageGenerationResult(image_bytes=b"harbor-view-1", mime_type="image/png", provider="mock", model="mock")
    with patch("services.theater_image_generation.get_image_provider", return_value=image):
        gen_res = builder.client.post(f"{base}/generate", json={
            "revision": result.json()["revision"],
            "kind": gen["kind"],
            "name": gen["name"],
            "prompt": gen["prompt"],
            "references": gen["references"],
        })
    assert gen_res.status_code == 200
    scene_path1 = gen_res.json()["path"]
    assert scene_path1 == "references/scenes/Old Harbor/1.png"
    assert builder.client.get(f"{base}/file", params={"path": scene_path1}).content == b"harbor-view-1"

    # Second generation for the same scene increments iteration to 2.png
    image.generate.return_value = ImageGenerationResult(image_bytes=b"harbor-view-2", mime_type="image/png", provider="mock", model="mock")
    with patch("services.theater_image_generation.get_image_provider", return_value=image):
        gen_res2 = builder.client.post(f"{base}/generate", json={
            "revision": gen_res.json()["state"]["draft"]["revision"],
            "kind": gen["kind"],
            "name": gen["name"],
            "prompt": "Harbor at sunset",
            "references": [scene_path1],
        })
    assert gen_res2.status_code == 200
    scene_path2 = gen_res2.json()["path"]
    assert scene_path2 == "references/scenes/Old Harbor/2.png"
    assert builder.client.get(f"{base}/file", params={"path": scene_path2}).content == b"harbor-view-2"

    # Verify deployment publishes scenes to the live theater
    published = builder.client.post(f"{base}/deploy", json={"revision": gen_res2.json()["state"]["draft"]["revision"]})
    assert published.status_code == 200
    identifier = data["draft"]["theater_id"]
    assert (builder.repository.theater_path(identifier) / scene_path1).read_bytes() == b"harbor-view-1"
    assert (builder.repository.theater_path(identifier) / scene_path2).read_bytes() == b"harbor-view-2"


def test_failed_or_unaffordable_generation_does_not_charge(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    body = {"revision": data["draft"]["revision"], "kind": "reference", "name": "hero", "prompt": "Hero"}
    builder.database.get_user_by_id.return_value = {"id": 7, "credits": 0.0}
    with patch("api_server.theater_editor.generate_asset") as generate:
        assert builder.client.post(f"{base}/generate", json=body).status_code == 402
    generate.assert_not_called()
    builder.database.get_user_by_id.return_value = {"id": 7, "credits": 10.0}
    with patch("api_server.theater_editor.generate_asset", side_effect=RuntimeError("provider failed")):
        assert builder.client.post(f"{base}/generate", json=body).status_code == 502
    builder.database.record_user_usage.assert_not_called()


def test_assistant_proposes_then_applies_to_shared_checkout_without_saving(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    proposal = BuilderProposal(message="Add a guide", writes=[{"path": "lore/guide.txt", "content": "Harbor guide"}])
    builder.database.record_user_usage.return_value = {"credits": 29.9}
    with patch.object(TheaterBuilderStore, "propose", return_value=proposal), patch("api_server.theater_editor.auth_session_cache.invalidate_user") as invalidate:
        result = builder.client.post(f"{base}/assistant", json={"prompt": "Add a guide"})
    assert result.status_code == 200
    assert result.json()["credits_charged"] == 0.1
    assert result.json()["credits"] == 29.9
    builder.database.record_user_usage.assert_called_once()
    assert builder.database.record_user_usage.call_args.args == (7,)
    usage = builder.database.record_user_usage.call_args.kwargs
    assert usage["credit_cost"] == 0.1
    assert usage["idempotency_key"].startswith(f"builder:assistant:{data['draft']['theater_id']}:")
    invalidate.assert_called_once_with(7)
    assert builder.client.get(f"{base}/file?path=lore/guide.txt").status_code == 404
    applied = builder.client.post(f"{base}/apply", json=result.json())
    assert applied.status_code == 200, applied.text
    assert builder.client.get(f"{base}/file?path=lore/guide.txt").text == "Harbor guide"
    assert (builder.manager.theater(data["draft"]["theater_id"]).directory() / "lore/guide.txt").read_text(encoding="utf-8") == "Harbor guide"
    assert not (builder.repository.theater_path(data["draft"]["theater_id"]) / "lore/guide.txt").exists()
    builder.database.record_user_usage.assert_called_once()


@pytest.mark.parametrize("credits", [0.0, 0.09])
def test_unaffordable_assistant_does_not_call_provider_or_charge(builder: BuilderHarness, credits: float) -> None:
    data = builder.create()
    builder.database.get_user_by_id.return_value = {"id": 7, "credits": credits}
    with patch.object(TheaterBuilderStore, "propose") as propose:
        result = builder.client.post(f"/api/theater-editor/{data['draft']['theater_id']}/assistant", json={"prompt": "Write lore"})
    assert result.status_code == 402
    assert "0.1 credits" in result.json()["detail"]
    propose.assert_not_called()
    builder.database.record_user_usage.assert_not_called()


def test_failed_assistant_does_not_charge(builder: BuilderHarness) -> None:
    data = builder.create()
    with patch.object(TheaterBuilderStore, "propose", side_effect=ValueError("invalid proposal")):
        result = builder.client.post(f"/api/theater-editor/{data['draft']['theater_id']}/assistant", json={"prompt": "Write lore"})
    assert result.status_code == 502
    assert "No credits were charged" in result.json()["detail"]
    builder.database.record_user_usage.assert_not_called()


def test_assistant_billing_failure_withholds_proposal(builder: BuilderHarness) -> None:
    data = builder.create()
    builder.database.record_user_usage.side_effect = RuntimeError("database unavailable")
    with patch.object(TheaterBuilderStore, "propose", return_value=BuilderProposal(message="New lore")):
        result = builder.client.post(f"/api/theater-editor/{data['draft']['theater_id']}/assistant", json={"prompt": "Write lore"})
    assert result.status_code == 503
    assert "proposal" not in result.json()


@pytest.mark.parametrize("rate", [0.1, 0.2])
def test_each_assistant_reply_charges_configured_rate_with_unique_key(builder: BuilderHarness, rate: float) -> None:
    data = builder.create()
    builder.database.get_user_by_id.return_value = {"id": 7, "credits": rate}
    pricing = PricingController(theater_editor_assistant_credit_rate=rate)
    with patch.object(object_registry, "pricing_controller", pricing), patch.object(TheaterBuilderStore, "propose", return_value=BuilderProposal(message="Ideas")):
        assert builder.client.get(f"/api/theater-editor/{data['draft']['theater_id']}").json()["rates"]["theater_editor_assistant_credit_rate"] == rate
        for _ in range(2):
            result = builder.client.post(f"/api/theater-editor/{data['draft']['theater_id']}/assistant", json={"prompt": "Suggest ideas"})
            assert result.status_code == 200
            assert result.json()["credits_charged"] == rate
    calls = builder.database.record_user_usage.call_args_list
    assert len(calls) == 2
    assert all(call.kwargs["credit_cost"] == rate for call in calls)
    assert calls[0].kwargs["idempotency_key"] != calls[1].kwargs["idempotency_key"]


@pytest.mark.asyncio
@pytest.mark.parametrize("second_action", ["assistant", "generate"])
async def test_paid_actions_share_owner_lock_across_drafts(builder: BuilderHarness, second_action: str) -> None:
    first = builder.create()
    second = builder.create()
    started, release = Event(), Event()
    account = {"id": 7, "credits": 0.1}
    builder.database.get_user_by_id.return_value = account

    def propose(info: DraftInfo, prompt: str, history: list[ChatMessage], config: dict[str, JsonValue]) -> BuilderProposal:
        started.set()
        assert release.wait(timeout=5)
        return BuilderProposal(message="Ideas")

    def settle(user_id: int, *, credit_cost: float, idempotency_key: str) -> dict[str, float]:
        account["credits"] -= credit_cost
        return {"credits": account["credits"]}

    builder.database.record_user_usage.side_effect = settle
    pricing = PricingController(image_credit_rate=0.1)
    with patch.object(object_registry, "pricing_controller", pricing), patch("api_server.theater_editor._billing_locks", {}), patch.object(TheaterBuilderStore, "propose", side_effect=propose) as provider, patch("api_server.theater_editor.generate_asset") as generate:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers={"x-test-user": "7"}) as client:
            first_request = asyncio.create_task(client.post(f"/api/theater-editor/{first['draft']['theater_id']}/assistant", json={"prompt": "Write lore"}))
            try:
                assert await asyncio.to_thread(started.wait, 5)
                body = {"prompt": "Write lore"} if second_action == "assistant" else {"revision": second["draft"]["revision"], "kind": "reference", "name": "hero", "prompt": "Hero"}
                second_request = asyncio.create_task(client.post(f"/api/theater-editor/{second['draft']['theater_id']}/{second_action}", json=body))
                await asyncio.sleep(0)
            finally:
                release.set()
            results = await asyncio.gather(first_request, second_request)
    assert [result.status_code for result in results] == [200, 402]
    provider.assert_called_once()
    generate.assert_not_called()
    builder.database.record_user_usage.assert_called_once()


def test_default_draft_deploys_and_redeployment_keeps_identity(builder: BuilderHarness) -> None:
    data = builder.create(default=True)
    identifier = data["draft"]["theater_id"]
    base = f"/api/theater-editor/{identifier}"
    result = builder.client.post(f"{base}/deploy", json={"revision": data["draft"]["revision"]})
    assert result.status_code == 200, result.text
    assert result.json()["canvas_url"] == f"/canvas?theater_id={identifier}&role=orator"
    original_key = builder.manager.get_theater(identifier).join_key
    builder.database.get_deployment.return_value = {"user_id": 7, "theater_id": identifier, "join_key": original_key}
    second = builder.client.post(f"{base}/deploy", json={"revision": data["draft"]["revision"]})
    assert second.status_code == 200, second.text
    assert builder.manager.get_theater(identifier).join_key == original_key
    builder.database.record_deployment.assert_called_once()
    assert builder.repository.theater_path(identifier).joinpath("theater.json").exists()


def test_existing_theater_requires_owner_before_reconstruction(builder: BuilderHarness) -> None:
    builder.database.get_deployment.return_value = {"user_id": 8, "theater_id": "theater_owned"}
    with patch.object(builder.repository, "reconstruct_theater") as reconstruct:
        assert builder.client.get("/api/theater-editor/theater_owned").status_code == 403
    reconstruct.assert_not_called()


def test_editing_existing_theater_updates_shared_checkout_before_saving(builder: BuilderHarness) -> None:
    identifier = "theater_existing"
    image = png_bytes()
    metadata = builder.manager.create_theater(name="Old world", theater_id=identifier,
        reference_files=[("references/hero.png", image)], theater_config={"live_agent": {"special_instructions": "Old world"}})
    builder.repository.export_theater(identifier, builder.manager.theater(identifier).directory())
    builder.database.get_deployment.return_value = {"user_id": 7, "theater_id": identifier, "join_key": metadata.join_key}
    base = f"/api/theater-editor/{identifier}"
    opened = builder.client.get(base)
    assert opened.status_code == 200
    data = opened.json()
    result = builder.client.post(f"{base}/apply", json={"revision": data["draft"]["revision"], "proposal": {
        "message": "Organize the hero", "moves": [{"source": "references/hero.png", "destination": "references/people/hero.png"}],
        "writes": [{"path": "lore/hero.txt", "content": "image_reference: references/people/hero.png"}],
    }})
    assert result.status_code == 200, result.text
    target = builder.manager.theater(identifier).directory()
    assert not (target / "references/hero.png").exists()
    assert (target / "references/people/hero.png").exists()
    assert (builder.repository.theater_path(identifier) / "references/hero.png").exists()
    deployed = builder.client.post(f"{base}/deploy", json={"revision": result.json()["draft"]["revision"]})
    assert deployed.status_code == 200, deployed.text
    assert not (target / "references/hero.png").exists()
    assert (target / "references/people/hero.png").read_bytes() == image
    assert not (builder.repository.theater_path(identifier) / "references/hero.png").exists()
    assert builder.manager.get_theater(identifier).join_key == metadata.join_key


def test_new_editor_theater_is_saved_and_can_be_restored_after_checkout_loss(builder: BuilderHarness) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    root = builder.manager.theater(identifier).directory()
    canonical = builder.repository.theater_path(identifier)
    assert (root / "theater.json").is_file()
    assert (canonical / "theater.yaml").read_bytes() == (root / "theater.yaml").read_bytes()
    assert not (canonical / "editor.json").exists()
    assert data["draft"]["saved_revision"] == data["draft"]["revision"]
    join_key = builder.manager.get_theater(identifier).join_key
    base = f"/api/theater-editor/{identifier}"
    changed = builder.client.post(f"{base}/save", json={
        "revision": data["draft"]["revision"], "name": "Harbor", "writes": [{"path": "lore/unsaved.txt", "content": "temporary"}],
    })
    assert changed.status_code == 200, changed.text
    assert not (canonical / "lore/unsaved.txt").exists()
    builder.manager.destroy_theater(identifier)
    reopened = builder.client.get(base)
    assert reopened.status_code == 200, reopened.text
    assert not (root / "lore/unsaved.txt").exists()
    assert builder.manager.get_theater(identifier).join_key == join_key
    assert reopened.json()["draft"]["saved_revision"] == reopened.json()["draft"]["revision"]


def test_canvas_save_commits_editor_changes_and_removals_from_shared_checkout(builder: BuilderHarness) -> None:
    from api_server.theaters import _export_canvas_theater_async

    data = builder.create()
    identifier = data["draft"]["theater_id"]
    base = f"/api/theater-editor/{identifier}"
    service = object_registry.canvas_states
    state = service.get(identifier)
    connections = state.connections
    state.chat.messages = [{"text": "Keep this canvas history"}]
    state.persist()
    original_key = builder.manager.get_theater(identifier).join_key
    # Seed an asset in both places, then move it only in the working copy.
    uploaded = builder.client.post(f"{base}/upload", data={"revision": data["draft"]["revision"]}, files={"files": ("hero.png", png_bytes())})
    assert uploaded.status_code == 200, uploaded.text
    assert asyncio.run(_export_canvas_theater_async(identifier))
    revision = uploaded.json()["draft"]["revision"]
    changed = builder.client.post(f"{base}/apply", json={"revision": revision, "proposal": {
        "message": "Update the world", "writes": [{"path": "theater.yaml", "content": "live_agent:\n  special_instructions: Changed world\n"}],
        "moves": [{"source": "references/hero.png", "destination": "references/people/hero.png"}],
    }})
    assert changed.status_code == 200, changed.text
    # Canvas reads the edit immediately, without the editor's deploy endpoint.
    assert builder.manager.theater(identifier).config()["live_agent"]["special_instructions"] == "Changed world"
    assert service.get(identifier) is state
    assert state.connections is connections
    assert state.chat.messages == [{"text": "Keep this canvas history"}]
    canonical = builder.repository.theater_path(identifier)
    assert (canonical / "references/hero.png").exists()
    assert asyncio.run(_export_canvas_theater_async(identifier))
    assert not (canonical / "references/hero.png").exists()
    assert (canonical / "references/people/hero.png").read_bytes() == png_bytes()
    saved = builder.client.get(base).json()["draft"]
    assert saved["saved_revision"] == saved["revision"]
    assert builder.manager.get_theater(identifier).join_key == original_key
    assert json.loads((canonical / "theater.json").read_text(encoding="utf-8"))["canvas_state"]["chat_messages"] == state.chat.messages


def test_editor_explicit_save_commits_checkout_and_reports_export_failure(builder: BuilderHarness) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    base = f"/api/theater-editor/{identifier}"
    body = {"revision": data["draft"]["revision"], "name": "Saved Harbor", "persist": True,
            "writes": [{"path": "lore/guide.txt", "content": "Keep this"}]}
    saved = builder.client.post(f"{base}/save", json=body)
    assert saved.status_code == 200, saved.text
    info = saved.json()["draft"]
    assert info["saved_revision"] == info["revision"]
    assert (builder.repository.theater_path(identifier) / "lore/guide.txt").read_text(encoding="utf-8") == "Keep this"
    body["revision"] = info["revision"]
    body["writes"] = [{"path": "lore/guide.txt", "content": "Still working"}]
    with patch.object(builder.repository, "export_theater", return_value=False):
        failed = builder.client.post(f"{base}/save", json=body)
    assert failed.status_code == 503
    assert (builder.repository.theater_path(identifier) / "lore/guide.txt").read_text(encoding="utf-8") == "Keep this"
    reopened = builder.client.get(base).json()["draft"]
    assert reopened["saved_revision"] < reopened["revision"]
    assert (builder.manager.theater(identifier).directory() / "lore/guide.txt").read_text(encoding="utf-8") == "Still working"


def test_opening_existing_theater_preserves_characters_folder_and_portraits(builder: BuilderHarness) -> None:
    identifier = "theater_with_chars"
    image = png_bytes()
    metadata = builder.manager.create_theater(
        name="Cast world",
        theater_id=identifier,
        reference_files=[
            ("references/stage.png", image),
            ("references/characters/Arthur Modella/1.png", image),
        ],
        theater_config={"live_agent": {"special_instructions": "Cast world"}},
    )
    builder.repository.export_theater(identifier, builder.manager.theater(identifier).directory())
    builder.database.get_deployment.return_value = {"user_id": 7, "theater_id": identifier, "join_key": metadata.join_key}
    base = f"/api/theater-editor/{identifier}"

    opened = builder.client.get(base)
    assert opened.status_code == 200, opened.text
    data = opened.json()
    paths = {f["path"] for f in data["files"]}
    assert "references/characters/Arthur Modella/1.png" in paths

    # Verify character file can be read from draft endpoint
    read_res = builder.client.get(f"{base}/file?path=references/characters/Arthur%20Modella/1.png")
    assert read_res.status_code == 200
    assert read_res.content == image

    # Reopening an existing draft also preserves characters
    reopened = builder.client.get(base)
    assert reopened.status_code == 200
    reopened_paths = {f["path"] for f in reopened.json()["files"]}
    assert "references/characters/Arthur Modella/1.png" in reopened_paths


def test_organized_assets_are_listed_served_and_resolved_on_canvas(builder: BuilderHarness) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    base = f"/api/theater-editor/{identifier}"
    uploaded = builder.client.post(f"{base}/upload", data={"revision": data["draft"]["revision"], "folder": "true"}, files=[
        ("files", ("world/theater.yaml", b"live_agent: {}\nvisuals:\n  cycle_length: 0\nstarting_image: references/people/hero.png\n")),
        ("files", ("world/references/people/hero.png", b"image")),
        ("files", ("world/playlists/Boss fight/chapter 1/theme#1.aac", b"audio")),
        ("files", ("world/playlists/Boss fight/description.txt", b"Escalating tension")),
    ])
    assert uploaded.status_code == 200, uploaded.text
    deployed = builder.client.post(f"{base}/deploy", json={"revision": uploaded.json()["draft"]["revision"]})
    assert deployed.status_code == 200, deployed.text
    theater = builder.manager.theater(identifier)
    hero = theater.references_dir() / "people/hero.png"
    assert theater.get_url_for_path(str(hero)) == f"/theaters/{identifier}/references/people/hero.png"
    assert VisualState._find_starting_reference(theater, "references/people/hero.png") == hero.resolve()
    visual = VisualState(theater)
    assert visual.resolve_image_path("references/people/hero.png") == str(hero)
    tools = MusicTools(theater, CanvasStateService(builder.manager).get(identifier), MagicMock(spec=MusicCatalog))
    tracks = tools._resolve_music_tracks("Boss fight")
    assert tracks == [f"/theaters/{identifier}/playlists/Boss%20fight/chapter%201/theme%231.aac"]
    assert "chapter 1/theme#1.aac" in get_playlists_context(theater)
    assert "Escalating tension" in get_playlists_context(theater)
    builder.database.get_deployment.return_value = {"user_id": 7, "theater_id": identifier, "join_key": theater.metadata.join_key}
    builder.database.record_theater_view_async = AsyncMock()
    with patch("api_server.shared.get_current_user_async", new=AsyncMock(return_value={"id": 7})), patch("api_server.theaters.get_current_user_async", new=AsyncMock(return_value={"id": 7})):
        assert builder.client.get(tracks[0]).content == b"audio"
        metadata = builder.client.get(f"/api/theaters/{identifier}")
        assert metadata.status_code == 200, metadata.text
        assert metadata.json()["references"][0]["filename"] == "people/hero.png"
        assert metadata.json()["playlists"]["Boss fight"][0]["filename"] == "chapter 1/theme#1.aac"
        assert builder.client.get(f"/theaters/{identifier}/playlists/Boss%20fight/..%2F..%2Ftheater.yaml").status_code == 400


def test_billing_failure_removes_unsettled_generated_asset(builder: BuilderHarness) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    base = f"/api/theater-editor/{identifier}"
    builder.database.record_user_usage.side_effect = RuntimeError("database unavailable")
    with patch("api_server.theater_editor.generate_asset", return_value=("references/generated.png", b"image")):
        result = builder.client.post(f"{base}/generate", json={"revision": data["draft"]["revision"], "kind": "reference", "name": "hero", "prompt": "Hero"})
    assert result.status_code == 503
    assert builder.client.get(f"{base}/file?path=references/generated.png").status_code == 404


def test_stale_generation_request_cannot_change_or_charge_the_draft(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    with patch("api_server.theater_editor.generate_asset") as generate:
        result = builder.client.post(f"{base}/generate", json={"revision": -1, "kind": "reference", "name": "hero", "prompt": "Hero"})
    assert result.status_code == 409
    generate.assert_not_called()
    builder.database.record_user_usage.assert_not_called()


def test_editor_and_deploy_navigation_are_present(builder: BuilderHarness) -> None:
    assert builder.client.get("/theater-editor").status_code == 200
    html = builder.client.get("/deploy").text
    assert 'href="/theater-editor"' in html
    assert 'Edit in Builder' in html
    canvas = builder.client.get("/canvas").text
    assert 'id="menu-item-theater-builder"' in canvas
    assert "if (builderLink && isOwner)" in canvas


def test_import_google_drive_image_success(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    mock_result = GoogleImportResult(
        kind="image",
        suggested_path="references/hero_portrait.png",
        content_bytes=b"fake_image_bytes",
        title="hero_portrait",
    )
    with patch("api_server.theater_editor.import_google_link", new=AsyncMock(return_value=mock_result)):
        result = builder.client.post(f"{base}/google-link", json={
            "revision": data["draft"]["revision"],
            "url": "https://drive.google.com/file/d/IMG123/view",
            "target_name": "hero_portrait",
        })
    assert result.status_code == 200, result.text
    assert result.json()["kind"] == "image"
    assert result.json()["path"] == "references/hero_portrait.png"
    assert builder.client.get(f"{base}/file?path=references/hero_portrait.png").content == b"fake_image_bytes"


def test_import_google_doc_raw(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    mock_result = GoogleImportResult(
        kind="doc",
        suggested_path="lore/world_guide.txt",
        text_content="World setting guide",
        title="world_guide",
    )
    with patch("api_server.theater_editor.import_google_link", new=AsyncMock(return_value=mock_result)):
        result = builder.client.post(f"{base}/google-link", json={
            "revision": data["draft"]["revision"],
            "url": "https://docs.google.com/document/d/DOC123/edit",
            "harvest": False,
        })
    assert result.status_code == 200, result.text
    assert result.json()["kind"] == "doc"
    assert result.json()["harvested"] is False
    assert builder.client.get(f"{base}/file?path=lore/world_guide.txt").text == "World setting guide"


def test_import_google_doc_harvest(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    mock_result = GoogleImportResult(
        kind="doc",
        suggested_path="lore/campaign.txt",
        text_content="Full campaign world notes",
        title="campaign",
    )
    proposal = BuilderProposal(
        message="Harvested campaign doc",
        writes=[{"path": "lore/factions.txt", "content": "Three warring factions"}],
    )
    with patch("api_server.theater_editor.import_google_link", new=AsyncMock(return_value=mock_result)), patch.object(TheaterBuilderStore, "harvest_doc", return_value=proposal) as harvest_mock:
        result = builder.client.post(f"{base}/google-link", json={
            "revision": data["draft"]["revision"],
            "url": "https://docs.google.com/document/d/DOC_HARVEST/edit",
            "harvest": True,
            "harvest_prompt": "Focus on factions",
        })
    assert result.status_code == 200, result.text
    assert result.json()["kind"] == "doc"
    assert result.json()["harvested"] is True
    assert result.json()["proposal"]["message"] == "Harvested campaign doc"
    assert result.json()["credits_charged"] == 0.1
    harvest_mock.assert_called_once()
    builder.database.record_user_usage.assert_called_once()


def test_assistant_auto_imports_google_image_in_prompt(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    mock_result = GoogleImportResult(
        kind="image",
        suggested_path="references/captain.png",
        content_bytes=b"captain_image",
        title="captain",
    )
    proposal = BuilderProposal(message="Added captain reference")
    with patch("api_server.theater_editor.import_google_link", new=AsyncMock(return_value=mock_result)), patch.object(TheaterBuilderStore, "propose", return_value=proposal) as propose_mock:
        result = builder.client.post(f"{base}/assistant", json={
            "prompt": "Here is our captain: https://drive.google.com/file/d/IMG_CAPTAIN/view, make lore for him",
        })
    assert result.status_code == 200, result.text
    assert builder.client.get(f"{base}/file?path=references/captain.png").content == b"captain_image"
    propose_mock.assert_called_once()


@pytest.mark.parametrize("import_error", [ValueError("The Google file is not shared publicly."), RuntimeError("Download failed")])
def test_assistant_returns_google_import_errors_without_charging_or_proposing(builder: BuilderHarness, import_error: Exception) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    with patch("api_server.theater_editor.import_google_link", new=AsyncMock(side_effect=import_error)), patch.object(TheaterBuilderStore, "propose") as propose:
        result = builder.client.post(f"{base}/assistant", json={"prompt": "Use https://drive.google.com/file/d/asset_id_123/view"})
    assert result.status_code in {400, 502}
    assert "detail" in result.json()
    propose.assert_not_called()
    builder.database.record_user_usage.assert_not_called()
    assert builder.client.get(base).json()["draft"]["revision"] == data["draft"]["revision"]


def test_assistant_imports_multiple_links_atomically(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    image = GoogleImportResult(kind="image", title="Captain", suggested_path="references/captain.png", content_bytes=b"image")
    with patch("api_server.theater_editor.import_google_link", new=AsyncMock(side_effect=[image, ValueError("Unavailable file")])), patch.object(TheaterBuilderStore, "propose") as propose:
        result = builder.client.post(f"{base}/assistant", json={"prompt": "Use https://drive.google.com/file/d/first_asset_123/view and https://drive.google.com/file/d/second_asset_456/view"})
    assert result.status_code == 400, result.text
    assert builder.client.get(f"{base}/file?path=references/captain.png").status_code == 404
    propose.assert_not_called()
    builder.database.record_user_usage.assert_not_called()


def test_delete_endpoint_validates_and_removes_asset(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    # Create a lore file to delete
    save_result = builder.client.post(f"{base}/save", json={
        "revision": data["draft"]["revision"],
        "name": data["draft"]["name"],
        "writes": [{"path": "lore/obsolete.txt", "content": "To be removed"}],
    })
    assert save_result.status_code == 200
    rev = save_result.json()["draft"]["revision"]

    # Unauthorized requests
    builder.client.headers.pop("x-test-user")
    assert builder.client.post(f"{base}/delete", json={"revision": rev, "path": "lore/obsolete.txt"}).status_code == 401
    builder.client.headers["x-test-user"] = "999"
    assert builder.client.post(f"{base}/delete", json={"revision": rev, "path": "lore/obsolete.txt"}).status_code == 403
    builder.client.headers["x-test-user"] = "7"

    # Revision mismatch
    assert builder.client.post(f"{base}/delete", json={"revision": rev - 1, "path": "lore/obsolete.txt"}).status_code == 409

    # Cannot delete theater.yaml
    assert builder.client.post(f"{base}/delete", json={"revision": rev, "path": "theater.yaml"}).status_code == 400

    # Nonexistent file
    assert builder.client.post(f"{base}/delete", json={"revision": rev, "path": "lore/missing.txt"}).status_code == 400

    # Successful deletion
    del_result = builder.client.post(f"{base}/delete", json={"revision": rev, "path": "lore/obsolete.txt"})
    assert del_result.status_code == 200, del_result.text
    new_files = [f["path"] for f in del_result.json()["files"]]
    assert "lore/obsolete.txt" not in new_files
    assert del_result.json()["draft"]["revision"] == rev + 1
    assert builder.client.get(f"{base}/file?path=lore/obsolete.txt").status_code == 404


def test_delete_via_http_delete_method(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    save_result = builder.client.post(f"{base}/save", json={
        "revision": data["draft"]["revision"],
        "name": data["draft"]["name"],
        "writes": [{"path": "lore/temp.txt", "content": "temporary"}],
    })
    rev = save_result.json()["draft"]["revision"]
    del_result = builder.client.delete(f"{base}/file?path=lore/temp.txt&revision={rev}")
    assert del_result.status_code == 200
    assert "lore/temp.txt" not in [f["path"] for f in del_result.json()["files"]]


def test_apply_proposal_executes_deletions(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    save_result = builder.client.post(f"{base}/save", json={
        "revision": data["draft"]["revision"],
        "name": data["draft"]["name"],
        "writes": [{"path": "lore/old_lore.txt", "content": "old"}],
    })
    rev = save_result.json()["draft"]["revision"]
    proposal = BuilderProposal(
        message="Replace old lore with new lore",
        writes=[{"path": "lore/new_lore.txt", "content": "brand new lore"}],
        deletions=["lore/old_lore.txt"],
    )
    apply_result = builder.client.post(f"{base}/apply", json={
        "revision": rev,
        "proposal": proposal.model_dump(mode="json"),
    })
    assert apply_result.status_code == 200, apply_result.text
    paths = [f["path"] for f in apply_result.json()["files"]]
    assert "lore/new_lore.txt" in paths
    assert "lore/old_lore.txt" not in paths


def test_draft_generation_beyond_concurrency_cap_rejected_with_429(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    body = {"revision": data["draft"]["revision"], "kind": "reference", "name": "hero", "prompt": "Hero"}
    builder.database.acquire_generation_slot.return_value = None
    with patch("api_server.theater_editor.generate_asset") as generate:
        result = builder.client.post(f"{base}/generate", json=body)
    assert result.status_code == 429
    assert "Too many concurrent generation jobs" in result.json()["detail"]
    generate.assert_not_called()
    builder.database.record_user_usage.assert_not_called()


def test_draft_generations_generate_concurrently(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    both_generating = Barrier(2)
    account = {"id": 7, "credits": 4.0}
    builder.database.get_user_by_id.return_value = account

    def fake_generate(info: DraftInfo, body: GenerationRequest) -> tuple[str, bytes]:
        both_generating.wait(timeout=5)
        return (f"references/{body.name}.png", png_bytes())

    def settle(user_id: int, *, images_created: int = 0, music_created: int = 0, layered_animations_created: int = 0, credit_cost: float, idempotency_key: str) -> dict[str, float]:
        account["credits"] -= credit_cost
        return {"credits": account["credits"]}

    builder.database.record_user_usage.side_effect = settle
    with patch("api_server.theater_editor.generate_asset", side_effect=fake_generate), ThreadPoolExecutor(max_workers=2) as executor:
        def submit(name: str) -> int:
            return builder.client.post(
                f"{base}/generate",
                json={"revision": data["draft"]["revision"], "kind": "reference", "name": name, "prompt": name},
            ).status_code

        results = sorted(executor.map(submit, ["hero1", "hero2"]))
    assert results == [200, 200]
    assert builder.database.record_user_usage.call_count == 2
    assert account["credits"] == 4.0 - 2 * 4.0
    final_draft = builder.client.get(base).json()["draft"]
    assert final_draft["revision"] == data["draft"]["revision"] + 2


def test_download_draft_returns_zip_archive(builder: BuilderHarness) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    base = f"/api/theater-editor/{identifier}"

    builder.client.headers["x-test-user"] = "8"
    assert builder.client.get(f"{base}/download").status_code == 403
    builder.client.headers["x-test-user"] = "7"

    save_result = builder.client.post(f"{base}/save", json={
        "revision": data["draft"]["revision"],
        "name": "Custom Theater",
        "writes": [{"path": "lore/history.txt", "content": "Ancient history"}],
    })
    assert save_result.status_code == 200

    download_result = builder.client.get(f"{base}/download")
    assert download_result.status_code == 200
    assert download_result.headers["content-type"] == "application/zip"
    assert 'filename="Custom_Theater.zip"' in download_result.headers["content-disposition"]

    with zipfile.ZipFile(BytesIO(download_result.content), "r") as archive:
        names = archive.namelist()
        assert "theater.yaml" in names
        assert "lore/history.txt" in names
        assert archive.read("lore/history.txt") == b"Ancient history"


def test_reset_theater_removes_output_and_theater_json(builder: BuilderHarness) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    base = f"/api/theater-editor/{identifier}"

    builder.client.headers["x-test-user"] = "8"
    assert builder.client.post(f"{base}/reset", json={"revision": data["draft"]["revision"]}).status_code == 403
    builder.client.headers["x-test-user"] = "7"

    theater_dir = builder.manager.theater(identifier).directory()
    output_dir = builder.manager.theater(identifier).output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / "scene.png").write_bytes(b"scene_data")
    (output_dir / "story_log.jsonl").write_text('{"event": "start"}', encoding="utf-8")

    metadata = builder.manager.get_theater(identifier)
    metadata.canvas_state = {"scene": 1}
    builder.manager._save_metadata(metadata)
    repo_theater_json = builder.repository.theater_path(identifier) / "theater.json"
    repo_theater_json.parent.mkdir(parents=True, exist_ok=True)
    repo_theater_json.write_text('{"theater_id": "test", "canvas_state": {"scene": 1}}', encoding="utf-8")

    assert (images_dir / "scene.png").is_file()
    assert (output_dir / "story_log.jsonl").is_file()
    assert (theater_dir / "theater.json").is_file()
    assert repo_theater_json.is_file()

    result = builder.client.post(f"{base}/reset", json={"revision": data["draft"]["revision"]})
    assert result.status_code == 200
    assert result.json()["draft"]["theater_id"] == identifier

    assert output_dir.is_dir()
    assert not (images_dir / "scene.png").exists()
    assert not (output_dir / "story_log.jsonl").exists()
    assert list(output_dir.iterdir()) == []
    assert builder.manager.get_theater(identifier).canvas_state == {}
    assert json.loads(repo_theater_json.read_text(encoding="utf-8"))["canvas_state"] == {}

    file_result = builder.client.get(f"{base}/file?path=theater.yaml")
    assert file_result.status_code == 200

    deploy_result = builder.client.post(f"{base}/deploy", json={"revision": data["draft"]["revision"]})
    assert deploy_result.status_code == 200
    assert (theater_dir / "theater.json").is_file()


def test_reset_removes_image_updates_and_cached_canvas_history(builder: BuilderHarness) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    base = f"/api/theater-editor/{identifier}"
    identifiers = [identifier]
    roots = []
    for theater_id in identifiers:
        roots.extend([builder.manager.theater(theater_id).directory(), builder.repository.theater_path(theater_id)])
    for root in roots:
        for folder in ("characters/Hero", "scenes/Harbor", "props/Boat"):
            reference = root / "references" / folder
            reference.mkdir(parents=True, exist_ok=True)
            for filename in ("1.webp", "1.png", "2.webp", "10.webp", "3.png", "portrait.webp", "scene.yaml"):
                (reference / filename).write_bytes(b"original")
        (root / "theater.yaml").write_text("starting_image: references/scenes/Harbor/1.webp\nlive_agent: {}\n", encoding="utf-8")
        (root / "theater_state.json").write_text('{"canvas_state": {"narration": "old history"}}', encoding="utf-8")
        output = root / "output" / "artifacts" / "updated_references" / "scenes" / "Harbor"
        output.mkdir(parents=True, exist_ok=True)
        (output / "2.webp").write_bytes(b"updated")

    service = CanvasStateService(builder.manager)
    states = [service.get(theater_id) for theater_id in identifiers]
    for state in states:
        state.visual.shown_images_history = ["old.webp"]
        state.chat.messages = [{"text": "old chat"}]
        state.persist()
    connections = [state.connections for state in states]

    def stop_session(theater_id: str) -> bool:
        service.get(theater_id).persist()
        return True

    with patch.object(object_registry, "canvas_states", service), patch.object(object_registry.live_agent_manager, "stop_session", side_effect=stop_session):
        conflict = builder.client.post(f"{base}/reset", json={"revision": data["draft"]["revision"] + 1})
        assert conflict.status_code == 409
        assert (roots[0] / "references/scenes/Harbor/2.webp").is_file()
        result = builder.client.post(f"{base}/reset", json={"revision": data["draft"]["revision"]})
    assert result.status_code == 200, result.text
    for root in roots:
        for folder in ("characters/Hero", "scenes/Harbor", "props/Boat"):
            reference = root / "references" / folder
            assert {item.name for item in reference.iterdir()} == {"1.webp", "1.png", "portrait.webp", "scene.yaml"}
            assert (reference / "1.webp").read_bytes() == b"original"
        assert json.loads((root / "theater.json").read_text(encoding="utf-8"))["canvas_state"] == {}
        assert not (root / "theater_state.json").exists()
        assert list((root / "output").iterdir()) == []
    for state, connection in zip(states, connections):
        assert service.get(state.theater_id) is state
        assert state.connections is connection
        assert state.serialized_state()["shown_images_history"] == []
        assert state.serialized_state()["chat_messages"] == []
        assert state.serialized_state()["narration"] == ""
        state.persist()
        saved = json.loads((state.theater.directory() / "theater.json").read_text(encoding="utf-8"))
        assert saved["canvas_state"]["shown_images_history"] == []
        assert saved["canvas_state"]["chat_messages"] == []
        assert saved["canvas_state"]["narration"] == ""

    deploy_result = builder.client.post(f"{base}/deploy", json={"revision": data["draft"]["revision"]})
    assert deploy_result.status_code == 200, deploy_result.text
    builder.database.get_deployment.return_value = {"user_id": 7}
    reopened = builder.client.get(base)
    assert reopened.status_code == 200, reopened.text
    paths = {item["path"] for item in reopened.json()["files"]}
    assert "references/scenes/Harbor/1.webp" in paths
    assert "references/scenes/Harbor/2.webp" not in paths
    assert "references/characters/Hero/10.webp" not in paths



@pytest.mark.parametrize("technique", ["video", "layered", "triframe"])
def test_editor_generates_all_animation_files_and_charges_once(builder: BuilderHarness, technique: str) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    files = {
        f"animations/rain/{technique}.json": b'{"scene_prompt":"Rain"}',
        "animations/rain/frame_1.webp": png_bytes(),
        "animations/rain/frame_2.webp": png_bytes(),
    }
    with patch("api_server.theater_editor.generate_theater_media", return_value=files) as generate:
        result = builder.client.post(f"/api/theater-editor/{identifier}/generate", json={
            "revision": data["draft"]["revision"], "kind": "animation",
            "name": "rain", "prompt": "Rain", "animation_type": technique,
        })
    assert result.status_code == 200, result.text
    assert generate.call_args.kwargs["animation_type"] == technique
    assert generate.call_args.kwargs["kind"] == "animation"
    for filename, content in files.items():
        assert (builder.manager.theater(identifier).directory() / filename).read_bytes() == content
    usage = builder.database.record_user_usage
    usage.assert_called_once()
    assert usage.call_args.kwargs["layered_animations_created"] == 1
    assert usage.call_args.kwargs["images_created"] == 0
    assert usage.call_args.kwargs["music_created"] == 0
    paths = {item["path"] for item in result.json()["state"]["files"]}
    assert files.keys() <= paths


def test_editor_animation_billing_failure_removes_every_generated_file(builder: BuilderHarness) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    files = {
        "animations/rain/video.json": b'{"scene_prompt":"Rain"}',
        "animations/rain/video.mp4": b"video",
    }
    builder.database.record_user_usage.side_effect = RuntimeError("billing unavailable")
    with patch("api_server.theater_editor.generate_theater_media", return_value=files):
        result = builder.client.post(f"/api/theater-editor/{identifier}/generate", json={
            "revision": data["draft"]["revision"], "kind": "animation", "name": "rain", "prompt": "Rain",
        })
    assert result.status_code == 503
    for filename in files:
        assert not (builder.manager.theater(identifier).directory() / filename).exists()
