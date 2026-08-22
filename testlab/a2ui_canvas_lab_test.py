from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from providers import TextResponseResult
from testlab.a2ui_canvas_lab import run_health_bars_smoke
from testlab.server import app


def test_health_bars_lab_creates_and_validates_surface():
    provider = MagicMock()
    provider.generate.return_value = TextResponseResult(
        text="",
        provider="test",
        model="test-model",
        parsed={
            "surfaces": [
                {"left_pct": 25, "top_pct": 70, "width_pct": 24, "persistent": True, "components": [
                    {"id": "root", "component": "Card", "child": "knight_1"},
                    {"id": "knight_1", "component": "Progress", "label": "Knight 1", "value": 100, "max": 100, "variant": "health"},
                ]},
                {"left_pct": 75, "top_pct": 70, "width_pct": 24, "persistent": True, "components": [
                    {"id": "root", "component": "Card", "child": "knight_2"},
                    {"id": "knight_2", "component": "Progress", "label": "Knight 2", "value": 100, "max": 100, "variant": "health"},
                ]},
            ],
        },
    )

    result = run_health_bars_smoke(provider)

    assert result["result"]["status"] == "displayed"
    assert result["errors"] == []
    assert len(result["surfaces"]) == 2
    request = provider.generate.call_args.args[0]
    assert request.response_schema is not None
    assert request.model == "gemini-3.7-flash"


def test_a2ui_canvas_server_accepts_flexible_configuration(monkeypatch):
    from testlab import server
    monkeypatch.setattr(server, "_run_a2ui_canvas_test", lambda *_: None)
    client = TestClient(app)

    page = client.get("/a2ui-canvas")
    assert page.status_code == 200
    assert "A2UI Canvas Lab" in page.text
    assert "Rendered preview" in page.text
    assert "translate(-50%, -50%)" in page.text
    # The server stores the test payload beneath run.result; the preview must
    # unwrap that envelope before looking for surfaces.
    assert "run?.result?.surfaces?run.result:run" in page.text

    images = client.get("/api/a2ui-canvas/images")
    assert images.status_code == 200
    image_path = next(image["path"] for image in images.json()["images"] if image["path"].endswith("trace-knight-sword.png"))
    image = client.get("/api/a2ui-canvas/image", params={"path": image_path})
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/png")

    defaults = client.get("/api/a2ui-canvas/default-config")
    assert defaults.status_code == 200
    assert defaults.json()["name"] == "a2ui-canvas-test"
    assert defaults.json()["expected_surface_count"] == 1
    assert "expected_component_counts" not in defaults.json()
    assert "expected_progress" not in defaults.json()

    response = client.post("/api/a2ui-canvas/runs", json={
        "model": "gemini-3.7-flash",
        "request": "Create a status panel.",
        "expected_surface_count": 1,
        "expected_persistent": False,
    })
    assert response.status_code == 200
    run = response.json()
    assert run["status"] == "running"
    assert run["config"]["expected_surface_count"] == 1
    assert run["config"]["expected_persistent"] is False
