"""Owner authorization, persistence, and billing for canvas stamp generation."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import Barrier
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request
from fastapi.testclient import TestClient
from PIL import Image
import pytest

import api_server.theaters as theaters
from components.theater_manager import TheaterManager
from pricing.pricing_controller import PricingController
from providers.image_provider import ImageGenerationRequest, ImageGenerationResult
from storage.theater_repository import TheaterRepository


@dataclass
class StampHarness:
    client: TestClient
    manager: TheaterManager
    repository: TheaterRepository
    database: MagicMock
    provider: MagicMock
    broadcast: AsyncMock


@pytest.fixture
def stamps(tmp_path: Path) -> Iterator[StampHarness]:
    manager = TheaterManager(tmp_path / "runtime")
    manager.create_theater("Stamps", "stage")
    root = manager.theater("stage").directory()
    (root / "theater.yaml").write_text("visuals:\n  style: Fantasy\n", encoding="utf-8")
    repository = TheaterRepository(tmp_path / "repository")
    repository.export_theater("stage", root)
    database = MagicMock()
    database.get_deployment.return_value = {"user_id": 7, "active_orator_id": 8, "contributors": "[9]"}
    database.get_user_by_id.return_value = {"id": 7, "credits": 10}
    database.record_user_usage.return_value = {"credits": 6}
    provider = MagicMock()
    buffer = BytesIO()
    image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    image.putpixel((16, 16), (255, 0, 0, 255))
    image.save(buffer, format="WEBP", quality=95)
    provider.generate.return_value = ImageGenerationResult(
        image_bytes=buffer.getvalue(), mime_type="image/webp", provider="mock", model="mock",
    )
    broadcast = AsyncMock()

    async def authenticate(request: Request) -> dict[str, int] | None:
        identifier = request.headers.get("x-test-user")
        return {"id": int(identifier)} if identifier else None

    with patch.object(theaters, "db", database), patch.object(theaters, "theater_manager", manager), \
         patch.object(theaters, "theater_repository", repository), \
         patch.object(theaters, "pricing_controller", PricingController(image_credit_rate=4)), \
         patch.object(theaters, "get_current_user_async", AsyncMock(side_effect=authenticate)), \
         patch.object(theaters, "_broadcast_doodle", broadcast), \
         patch.object(theaters, "canvas_states", MagicMock()), \
         patch("services.theater_image_generation.get_image_provider", return_value=provider), \
         patch.object(theaters.auth_session_cache, "invalidate_user"), \
         TestClient(theaters.app, headers={"x-test-user": "7"}) as client:
        yield StampHarness(client, manager, repository, database, provider, broadcast)


@pytest.mark.parametrize("asset", ["stamps", "scenes"])
@pytest.mark.parametrize(("user_id", "status"), [(None, 401), (8, 403), (9, 403), (10, 403)])
def test_only_owner_can_generate_not_orator_or_contributor(
    stamps: StampHarness, user_id: int | None, status: int, asset: str,
) -> None:
    stamps.client.headers.pop("x-test-user")
    if user_id is not None:
        stamps.client.headers["x-test-user"] = str(user_id)
    result = stamps.client.post(f"/api/theaters/stage/{asset}/generate", json={"name": "Goblin", "prompt": "Goblin"})
    assert result.status_code == status
    stamps.provider.generate.assert_not_called()
    stamps.database.record_user_usage.assert_not_called()


def test_owner_without_baton_generates_persistent_transparent_stamp(stamps: StampHarness) -> None:
    with patch("services.theater_image_generation.get_image_provider", return_value=stamps.provider) as resolve:
        result = stamps.client.post("/api/theaters/stage/stamps/generate", json={"name": "../Goblin", "prompt": "Goblin scout"})
    resolve.assert_called_once_with("openai-gpt-image-flare", {"model": "gpt-image-2.5-sunburst", "quality": "medium"})
    assert result.status_code == 200, result.text
    payload = result.json()
    stamp = payload["stamps"][0]
    filename = stamp["url"].rsplit("/", 1)[-1]
    assert stamp["id"] == f"theater:stage:{filename}"
    content = (stamps.manager.theater("stage").stamps_dir() / filename).read_bytes()
    assert (stamps.repository.theater_path("stage") / "stamps" / filename).read_bytes() == content
    with Image.open(BytesIO(content)) as image:
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0)) == (0, 0, 0, 0)
    request = stamps.provider.generate.call_args.args[0]
    assert request.background == "transparent"
    assert request.aspect_ratio == "1:1"
    assert payload["credits_charged"] == 4
    assert stamps.database.record_user_usage.call_args.args == (7,)
    assert stamps.database.record_user_usage.call_args.kwargs["images_created"] == 1
    assert stamps.broadcast.await_args.args[1] == {"type": "theater_stamps_updated", "stamps": payload["stamps"]}
    rebuilt = stamps.manager.base_dir / "rebuilt"
    assert stamps.repository.reconstruct_theater("stage", rebuilt)
    assert (rebuilt / "stamps" / filename).read_bytes() == content


@pytest.mark.parametrize("prompt", ["", "   ", "x" * 4001])
def test_invalid_prompt_is_rejected_without_generation(stamps: StampHarness, prompt: str) -> None:
    result = stamps.client.post("/api/theaters/stage/stamps/generate", json={"name": "Hero", "prompt": prompt})
    assert result.status_code == 422
    stamps.provider.generate.assert_not_called()


def test_unaffordable_or_failed_generation_does_not_charge(stamps: StampHarness) -> None:
    body = {"name": "Hero", "prompt": "Hero"}
    stamps.database.get_user_by_id.return_value = {"id": 7, "credits": 0}
    assert stamps.client.post("/api/theaters/stage/stamps/generate", json=body).status_code == 402
    stamps.provider.generate.assert_not_called()
    stamps.database.get_user_by_id.return_value = {"id": 7, "credits": 10}
    stamps.provider.generate.side_effect = RuntimeError("provider unavailable")
    assert stamps.client.post("/api/theaters/stage/stamps/generate", json=body).status_code == 502
    stamps.database.record_user_usage.assert_not_called()
    assert stamps.manager.theater("stage").stamps() == []


def test_billing_failure_removes_stamp_from_runtime_and_repository(stamps: StampHarness) -> None:
    stamps.database.record_user_usage.side_effect = RuntimeError("database unavailable")
    result = stamps.client.post("/api/theaters/stage/stamps/generate", json={"name": "Hero", "prompt": "Hero"})
    assert result.status_code == 503
    assert stamps.manager.theater("stage").stamps() == []
    assert list((stamps.repository.theater_path("stage") / "stamps").glob("*.webp")) == []
    stamps.broadcast.assert_not_awaited()


def test_storage_failure_removes_partial_stamp_without_charging(stamps: StampHarness) -> None:
    def partial_save(theater_id: str, filename: str, content: bytes) -> None:
        target = stamps.manager.theater(theater_id).stamps_dir() / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        raise OSError("repository unavailable")

    with patch.object(theaters, "_save_generated_stamp", side_effect=partial_save):
        result = stamps.client.post("/api/theaters/stage/stamps/generate", json={"name": "Hero", "prompt": "Hero"})
    assert result.status_code == 502
    assert stamps.manager.theater("stage").stamps() == []
    stamps.database.record_user_usage.assert_not_called()
    stamps.broadcast.assert_not_awaited()


@pytest.mark.parametrize("asset", ["stamps", "scenes"])
@pytest.mark.parametrize("credits", [10, 4])
def test_stamp_jobs_generate_concurrently_and_settle_with_bounded_overdraft(
    stamps: StampHarness, credits: int, asset: str,
) -> None:
    both_generating = Barrier(2)
    image = stamps.provider.generate.return_value
    account: dict[str, float] = {"id": 7, "credits": credits}
    stamps.database.get_user_by_id.return_value = account

    def generate(request: ImageGenerationRequest) -> ImageGenerationResult:
        # Neither request can finish generation until both have started.
        both_generating.wait(timeout=5)
        return image

    def settle(
        owner_id: int, *, images_created: int, credit_cost: float, idempotency_key: str,
    ) -> dict[str, float]:
        account["credits"] -= credit_cost
        return {"credits": account["credits"]}

    def submit(name: str) -> int:
        return stamps.client.post(
            f"/api/theaters/stage/{asset}/generate", json={"name": name, "prompt": name},
        ).status_code

    stamps.provider.generate.side_effect = generate
    stamps.database.record_user_usage.side_effect = settle
    # Each TestClient owns a fresh event loop; avoid reusing a previously bound lock.
    with patch.object(theaters, "_billing_locks", {}), ThreadPoolExecutor(max_workers=2) as executor:
        results = sorted(executor.map(submit, ["Goblin", "Dragon"]))
    expected_count = 2
    assert results == [200, 200]
    assert stamps.provider.generate.call_count == 2
    assert stamps.database.record_user_usage.call_count == expected_count
    saved = stamps.manager.theater("stage").stamps() if asset == "stamps" else stamps.manager.theater("stage").scenes()
    assert len(saved) == expected_count
    assert stamps.database.acquire_generation_slot.call_count == expected_count
    assert stamps.database.release_generation_slot.call_count == expected_count
    assert account["credits"] == credits - expected_count * 4


@pytest.mark.parametrize("asset", ["stamps", "scenes"])
def test_generation_beyond_concurrency_cap_rejected_with_429(stamps: StampHarness, asset: str) -> None:
    stamps.database.acquire_generation_slot.return_value = None
    result = stamps.client.post(
        f"/api/theaters/stage/{asset}/generate", json={"name": "Dragon", "prompt": "Dragon"},
    )
    assert result.status_code == 429
    assert "Too many concurrent generation jobs" in result.json()["detail"]
    stamps.provider.generate.assert_not_called()
    stamps.database.record_user_usage.assert_not_called()


@pytest.mark.parametrize(("scene_type", "instructions"), [
    ("cinematic", "Cinematic environment establishing shot"),
    ("tactical_map", "Strict top-down orthographic view"),
    ("regional_map", "Coherent geography with connected rivers"),
])
def test_scene_preset_saves_persistent_scene_and_charges_image_rate(
    stamps: StampHarness, scene_type: str, instructions: str,
) -> None:
    result = stamps.client.post("/api/theaters/stage/scenes/generate", json={
        "name": "../Forest", "prompt": "A forest clearing", "scene_type": scene_type,
    })
    assert result.status_code == 200, result.text
    payload = result.json()
    scene = payload["scenes"][0]
    path = Path(scene["path"])
    assert path.parent == stamps.manager.theater("stage").scenes_dir()
    assert path.read_bytes() == stamps.provider.generate.return_value.image_bytes
    assert (stamps.repository.theater_path("stage") / "references" / "scenes" / path.name).read_bytes() == path.read_bytes()
    request = stamps.provider.generate.call_args.args[0]
    assert request.prompt.startswith("A forest clearing\n")
    assert instructions in request.prompt
    assert "Style: Fantasy" not in request.prompt
    assert request.aspect_ratio == "16:9"
    assert request.background == "opaque"
    if scene_type == "tactical_map":
        assert "clearly visible square grid" in request.prompt
        assert "horizontal and vertical grid lines" in request.prompt
        assert "no grid" not in request.prompt
    if scene_type != "cinematic":
        assert "Establishing shot" not in request.prompt
        assert "Scenic environment" not in request.prompt
    assert payload["credits_charged"] == 4
    stamps.database.acquire_generation_slot.assert_called_once_with(
        7, "scene", "stage", theaters.MAX_CONCURRENT_GENERATION_JOBS, theaters.GENERATION_SLOT_TTL_SECONDS,
    )
    stamps.database.release_generation_slot.assert_called_once_with(stamps.database.acquire_generation_slot.return_value)
    assert stamps.database.record_user_usage.call_args.kwargs["images_created"] == 1
    assert stamps.broadcast.await_args.args[1] == {"type": "theater_scenes_updated", "scenes": payload["scenes"]}
    rebuilt = stamps.manager.base_dir / "rebuilt"
    assert stamps.repository.reconstruct_theater("stage", rebuilt)
    assert (rebuilt / "references" / "scenes" / path.name).read_bytes() == path.read_bytes()


@pytest.mark.parametrize("failure", ["credits", "provider", "billing", "storage"])
def test_scene_generation_failure_does_not_leave_a_scene(stamps: StampHarness, failure: str) -> None:
    expected_status = 502
    if failure == "credits":
        stamps.database.get_user_by_id.return_value = {"id": 7, "credits": 0}
        expected_status = 402
    elif failure == "provider":
        stamps.provider.generate.side_effect = RuntimeError("Provider failed")
    elif failure == "billing":
        stamps.database.record_user_usage.side_effect = RuntimeError("Billing failed")
        expected_status = 503

    def save_scene(theater_id: str, filename: str, content: bytes) -> None:
        target = stamps.manager.theater(theater_id).scenes_dir() / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        if failure == "storage":
            raise OSError("Repository unavailable")

    original_save = theaters._save_generated_scene
    with patch.object(theaters, "_save_generated_scene", side_effect=save_scene if failure == "storage" else original_save):
        result = stamps.client.post("/api/theaters/stage/scenes/generate", json={"name": "Forest", "prompt": "Forest"})
    assert result.status_code == expected_status
    assert stamps.manager.theater("stage").scenes() == []
    assert list((stamps.repository.theater_path("stage") / "references" / "scenes").glob("*.webp")) == []
    stamps.broadcast.assert_not_awaited()
    if failure != "billing":
        stamps.database.record_user_usage.assert_not_called()
    stamps.database.release_generation_slot.assert_called_once()


@pytest.mark.parametrize("body", [
    {"name": "Forest", "prompt": "Forest", "scene_type": "unknown"},
    {"name": "Forest", "prompt": "   "},
    {"name": "Forest", "prompt": "x" * 4001},
])
def test_invalid_scene_input_does_not_generate(stamps: StampHarness, body: dict[str, str]) -> None:
    assert stamps.client.post("/api/theaters/stage/scenes/generate", json=body).status_code == 422
    stamps.provider.generate.assert_not_called()


@pytest.mark.parametrize("explicit_none", [False, True])
def test_no_scene_preset_preserves_user_prompt_and_theater_style(
    stamps: StampHarness, explicit_none: bool,
) -> None:
    body = {"name": "Forest", "prompt": "A forest clearing"}
    if explicit_none:
        body["scene_type"] = "none"
    result = stamps.client.post("/api/theaters/stage/scenes/generate", json=body)
    assert result.status_code == 200, result.text
    request = stamps.provider.generate.call_args.args[0]
    assert request.prompt == "A forest clearing\nStyle: Fantasy"
