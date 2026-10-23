"""Personal stamp generation persists alpha images and settles standard credits."""

from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from api_server import profiles
from pricing.pricing_controller import PricingController
from providers.image_provider import ImageGenerationResult
from storage.stamp_storage import StampStorage


@dataclass
class ProfileStampHarness:
    client: TestClient
    database: MagicMock
    provider: MagicMock
    storage: StampStorage


@pytest.fixture
def personal_stamps(tmp_path: Path) -> Iterator[ProfileStampHarness]:
    database = MagicMock()
    database.count_user_stamps.return_value = 0
    database.acquire_generation_slot.return_value = "profile-job"
    database.get_user_by_id.return_value = {"id": 7, "credits": 10}
    database.create_user_stamp.return_value = {"id": 42, "user_id": 7, "name": "Goblin"}
    database.record_user_usage.return_value = {"credits": 6}
    provider = MagicMock()
    buffer = BytesIO()
    image = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    image.putpixel((256, 256), (255, 0, 0, 255))
    image.save(buffer, format="PNG")
    provider.generate.return_value = ImageGenerationResult(
        image_bytes=buffer.getvalue(), mime_type="image/png", provider="mock", model="mock",
    )
    with patch.object(StampStorage, "_init_gcs_client"):
        storage = StampStorage(tmp_path / "stamps")
    with (
        patch.object(profiles, "db", database),
        patch.object(profiles, "stamp_storage", storage),
        patch.object(profiles, "billing_locks", {}),
        patch.object(profiles, "pricing_controller", PricingController(image_credit_rate=4)),
        patch.object(profiles, "get_current_user_async", AsyncMock(return_value={"id": 7})),
        patch("services.theater_image_generation.get_image_provider", return_value=provider),
        patch.object(profiles.auth_session_cache, "invalidate_user"),
        TestClient(profiles.app) as client,
    ):
        yield ProfileStampHarness(client, database, provider, storage)


def test_generation_saves_transparent_personal_stamp_and_charges_image_rate(personal_stamps: ProfileStampHarness) -> None:
    harness = personal_stamps
    result = harness.client.post("/api/users/me/stamps/generate", json={"name": " Goblin ", "prompt": " Goblin scout "})
    assert result.status_code == 200, result.text
    assert result.json() == {
        "stamp": {"id": 42, "user_id": 7, "name": "Goblin", "url": "/api/stamps/42"},
        "credits_charged": 4, "credits": 6,
    }
    args = harness.database.create_user_stamp.call_args.args
    assert args[:2] == (7, "Goblin")
    content = harness.storage.read_stamp(7, args[2])
    assert content
    with Image.open(BytesIO(content)) as image:
        assert image.size == (256, 256)
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0
    request = harness.provider.generate.call_args.args[0]
    assert request.background == "transparent"
    assert request.aspect_ratio == "1:1"
    assert "Goblin scout" in request.prompt
    harness.database.record_user_usage.assert_called_once_with(
        7, images_created=1, credit_cost=4, idempotency_key=f"profile-stamp:7:42:{args[2]}",
    )
    harness.database.release_generation_slot.assert_called_once_with("profile-job")


@pytest.mark.parametrize("payload", [
    {"name": " ", "prompt": "Goblin"}, {"name": "Goblin", "prompt": " "},
    {"name": "x" * 101, "prompt": "Goblin"}, {"name": "Goblin", "prompt": "x" * 4001},
])
def test_invalid_generation_input(personal_stamps: ProfileStampHarness, payload: dict[str, str]) -> None:
    assert personal_stamps.client.post("/api/users/me/stamps/generate", json=payload).status_code == 422
    personal_stamps.provider.generate.assert_not_called()
    personal_stamps.database.record_user_usage.assert_not_called()


@pytest.mark.parametrize(("condition", "status"), [("anonymous", 401), ("full", 400), ("poor", 402), ("busy", 429)])
def test_generation_preconditions(personal_stamps: ProfileStampHarness, condition: str, status: int) -> None:
    harness = personal_stamps
    if condition == "full":
        harness.database.count_user_stamps.return_value = 10
    elif condition == "poor":
        harness.database.get_user_by_id.return_value = {"id": 7, "credits": 3}
    elif condition == "busy":
        harness.database.acquire_generation_slot.return_value = None
    with patch.object(profiles, "get_current_user_async", AsyncMock(return_value=None if condition == "anonymous" else {"id": 7})):
        result = harness.client.post("/api/users/me/stamps/generate", json={"name": "Goblin", "prompt": "Goblin"})
    assert result.status_code == status
    harness.provider.generate.assert_not_called()
    harness.database.record_user_usage.assert_not_called()
    if condition in {"poor", "full"}:
        harness.database.release_generation_slot.assert_called_once_with("profile-job")


@pytest.mark.parametrize("failure", ["provider", "invalid_image", "capacity", "save", "record", "billing"])
def test_failed_generation_does_not_leave_stamp(personal_stamps: ProfileStampHarness, failure: str) -> None:
    harness = personal_stamps
    if failure == "provider":
        harness.provider.generate.side_effect = RuntimeError("provider unavailable")
    elif failure == "invalid_image":
        harness.provider.generate.return_value = ImageGenerationResult(
            image_bytes=b"invalid", mime_type="image/webp", provider="mock", model="mock",
        )
    elif failure == "capacity":
        harness.database.count_user_stamps.side_effect = [9, 10]
    elif failure == "save":
        # Fail before storage writes, as a storage service outage would.
        harness.storage.save_stamp = MagicMock(side_effect=RuntimeError("storage unavailable"))
    elif failure == "record":
        harness.database.create_user_stamp.side_effect = ValueError("Maximum limit reached")
    elif failure == "billing":
        harness.database.record_user_usage.side_effect = RuntimeError("billing unavailable")
    result = harness.client.post("/api/users/me/stamps/generate", json={"name": "Goblin", "prompt": "Goblin"})
    assert result.status_code == {"capacity": 400, "record": 400, "billing": 503}.get(failure, 502)
    assert list(harness.storage.base_dir.rglob("*.webp")) == []
    if failure == "billing":
        harness.database.delete_user_stamp.assert_called_once_with(42, 7)
    else:
        harness.database.record_user_usage.assert_not_called()
    harness.database.release_generation_slot.assert_called_once_with("profile-job")
