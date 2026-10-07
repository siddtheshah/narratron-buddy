"""Regression coverage for uploaded documents executing under the app origin."""

import io
from pathlib import Path
from unittest.mock import AsyncMock, patch
import zipfile

import pytest
from fastapi.testclient import TestClient

from api_server import app
import api_server.theaters as theaters
from components.theater_manager import TheaterManager, extract_asset_package
from testing.reference_images import png_bytes


@pytest.mark.parametrize("filename", ["attack.html", "attack.svg", "attack.png"])
def test_manager_rejects_active_content_before_writing(tmp_path: Path, filename: str) -> None:
    manager = TheaterManager(tmp_path)
    with pytest.raises(ValueError):
        manager.create_theater("Attack", "attack", reference_files=[(filename, b"<script>alert(1)</script>")])
    assert not (tmp_path / "attack").exists()


def test_zip_references_cannot_bypass_validation(tmp_path: Path) -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("references/attack.html", "<script>alert(1)</script>")
    references, _, _, _ = extract_asset_package(archive.getvalue())
    with pytest.raises(ValueError):
        TheaterManager(tmp_path).create_theater("Attack", "attack", reference_files=references)


def test_reference_responses_sandbox_legacy_documents_and_display_images(tmp_path: Path) -> None:
    manager = TheaterManager(tmp_path)
    image = png_bytes()
    manager.create_theater("Stage", "stage", reference_files=[("hero.png", image)])
    root = manager.theater("stage").references_dir()
    (root / "attack.html").write_bytes(b"<script>alert(1)</script>")
    (root / "disguised.png").write_bytes(b"<script>alert(1)</script>")
    with patch.object(theaters, "theater_manager", manager), \
         patch.object(theaters, "_require_canvas_access_async", AsyncMock()), TestClient(app) as client:
        for filename in ("attack.html", "disguised.png"):
            response = client.get(f"/theaters/stage/references/{filename}")
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/octet-stream"
            assert response.headers["content-disposition"].startswith("attachment;")
            assert response.headers["x-content-type-options"] == "nosniff"
            assert "sandbox" in response.headers["content-security-policy"]
        response = client.get("/theaters/stage/references/hero.png")
        assert response.headers["content-type"] == "image/png"
        assert response.content == image


@pytest.mark.parametrize("field,filename", [
    ("reference_files", "attack.html"),
    ("asset_folder_files", "references/attack.html"),
])
def test_upload_returns_bad_request_for_active_references(tmp_path: Path, field: str, filename: str) -> None:
    manager = TheaterManager(tmp_path)
    with patch.object(theaters, "theater_manager", manager), \
         patch.object(theaters, "get_current_user_async", AsyncMock(return_value={"id": 1})), TestClient(app) as client:
        response = client.post("/api/theaters/create-and-deploy", files={field: (filename, b"<script>alert(1)</script>")})
    assert response.status_code == 400
    assert not list(tmp_path.glob("theater_*"))
