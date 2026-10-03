"""Integration coverage for authorization, imports, billing, and publishing drafts."""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import TypedDict
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic import JsonValue
import pytest

import object_registry
from api_server.shared import app
from components.canvas.canvas_state_service import CanvasStateService
from components.canvas.visual_state import VisualState
from components.theater_manager import TheaterManager
from pricing.pricing_controller import PricingController
from providers.image_provider import ImageGenerationResult
from providers.music_provider import MusicGenerationResult
from services.google_asset_importer import GoogleImportResult
from services.theater_builder import BuilderProposal, ChatMessage, DraftInfo, TheaterBuilderStore
from services.live_agent import get_playlists_context
from services.music_catalog import MusicCatalog
from storage.theater_repository import TheaterRepository
from tools.music_tool import MusicTools


class DraftInfoPayload(TypedDict):
    theater_id: str
    owner_id: int
    name: str
    source_id: str | None
    revision: int


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
    database.get_deployment.return_value = None
    database.get_user_by_id.return_value = {"id": 7, "credits": 30.0}
    database.record_user_usage.return_value = {"credits": 26.0}
    pricing = PricingController(image_credit_rate=4.0, music_credit_rate=6.0)

    async def authenticate(request: Request) -> dict[str, int] | None:
        identifier = request.headers.get("x-test-user")
        return {"id": int(identifier)} if identifier else None

    with patch.object(object_registry, "db", database), patch.object(object_registry, "theater_manager", manager), patch.object(object_registry, "theater_repository", repository), patch.object(object_registry, "pricing_controller", pricing), patch.object(object_registry, "live_agent_manager", MagicMock()), patch("api_server.theater_editor.get_current_user_async", new=AsyncMock(side_effect=authenticate)), TestClient(app, headers={"x-test-user": "7"}) as client:
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
    builder.database.record_user_usage.assert_not_called()


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


def test_stale_revision_cannot_overwrite_saved_changes(builder: BuilderHarness) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    body = {"revision": data["draft"]["revision"], "name": "Harbor", "writes": [{"path": "lore/opening.txt", "content": "Dawn"}]}
    assert builder.client.post(f"{base}/save", json=body).status_code == 200
    assert builder.client.post(f"{base}/save", json=body).status_code == 409
    assert builder.client.post(f"{base}/apply", json={"revision": body["revision"], "proposal": {"message": "Overwrite"}}).status_code == 409


@pytest.mark.parametrize(("kind", "cost", "counter"), [("reference", 4.0, "images_created"), ("playlist", 6.0, "music_created")])
def test_generated_assets_use_shared_live_providers_and_rates(builder: BuilderHarness, kind: str, cost: float, counter: str) -> None:
    data = builder.create()
    base = f"/api/theater-editor/{data['draft']['theater_id']}"
    image = MagicMock()
    image.generate.return_value = ImageGenerationResult(image_bytes=b"image", mime_type="image/png", provider="mock", model="mock")
    music = MagicMock()
    music.generate.return_value = MusicGenerationResult(audio_bytes=b"audio", mime_type="audio/mpeg", provider="mock", model="mock")
    with patch("api_server.theater_editor.get_image_provider", return_value=image), patch("api_server.theater_editor.get_music_provider", return_value=music):
        result = builder.client.post(f"{base}/generate", json={"revision": data["draft"]["revision"], "kind": kind, "name": "harbor", "prompt": "Misty harbor", "playlist": "ambient"})
    assert result.status_code == 200, result.text
    assert result.json()["credits_charged"] == cost
    usage = builder.database.record_user_usage.call_args.kwargs
    assert usage["credit_cost"] == cost
    assert usage[counter] == 1
    assert usage["idempotency_key"].startswith("builder:")
    assert builder.client.get(f"{base}/file", params={"path": result.json()["path"]}).content == (b"image" if kind == "reference" else b"audio")


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


def test_assistant_proposes_then_applies_without_modifying_runtime(builder: BuilderHarness) -> None:
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
    assert not builder.manager.theater(data["draft"]["theater_id"]).directory().exists()
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


def test_editing_existing_theater_preserves_live_assets_until_deployed(builder: BuilderHarness) -> None:
    identifier = "theater_existing"
    metadata = builder.manager.create_theater(name="Old world", theater_id=identifier,
        reference_files=[("references/hero.png", b"image")], theater_config={"live_agent": {"special_instructions": "Old world"}})
    builder.repository.export_theater(identifier, builder.manager.theater(identifier).directory())
    builder.database.get_deployment.return_value = {"user_id": 7, "theater_id": identifier, "join_key": metadata.join_key}
    base = f"/api/theater-editor/{identifier}"
    opened = builder.client.get(base)
    assert opened.status_code == 200
    data = opened.json()
    result = builder.client.post(f"{base}/apply", json={"revision": data["draft"]["revision"], "proposal": {
        "message": "Organize the hero", "moves": [{"source": "references/hero.png", "destination": "references/characters/hero.png"}],
        "writes": [{"path": "lore/hero.txt", "content": "image_reference: references/characters/hero.png"}],
    }})
    assert result.status_code == 200, result.text
    target = builder.manager.theater(identifier).directory()
    assert (target / "references/hero.png").exists()
    assert not (target / "references/characters/hero.png").exists()
    deployed = builder.client.post(f"{base}/deploy", json={"revision": result.json()["draft"]["revision"]})
    assert deployed.status_code == 200, deployed.text
    assert not (target / "references/hero.png").exists()
    assert (target / "references/characters/hero.png").read_bytes() == b"image"
    assert not (builder.repository.theater_path(identifier) / "references/hero.png").exists()
    assert builder.manager.get_theater(identifier).join_key == metadata.join_key


def test_organized_assets_are_listed_served_and_resolved_on_canvas(builder: BuilderHarness) -> None:
    data = builder.create()
    identifier = data["draft"]["theater_id"]
    base = f"/api/theater-editor/{identifier}"
    uploaded = builder.client.post(f"{base}/upload", data={"revision": data["draft"]["revision"], "folder": "true"}, files=[
        ("files", ("world/theater.yaml", b"live_agent: {}\nvisuals:\n  cycle_length: 0\nstarting_image: references/characters/hero.png\n")),
        ("files", ("world/references/characters/hero.png", b"image")),
        ("files", ("world/playlists/Boss fight/chapter 1/theme#1.aac", b"audio")),
        ("files", ("world/playlists/Boss fight/description.txt", b"Escalating tension")),
    ])
    assert uploaded.status_code == 200, uploaded.text
    deployed = builder.client.post(f"{base}/deploy", json={"revision": uploaded.json()["draft"]["revision"]})
    assert deployed.status_code == 200, deployed.text
    theater = builder.manager.theater(identifier)
    hero = theater.references_dir() / "characters/hero.png"
    assert theater.get_url_for_path(str(hero)) == f"/theaters/{identifier}/references/characters/hero.png"
    assert VisualState._find_starting_reference(theater, "references/characters/hero.png") == hero.resolve()
    visual = VisualState(theater)
    assert visual.resolve_image_path("references/characters/hero.png") == str(hero)
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
        assert metadata.json()["references"][0]["filename"] == "characters/hero.png"
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
