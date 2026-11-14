"""Tests for Test Lab static mounts and ES module loading."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from playwright.sync_api import Request, Response, Route, sync_playwright

from testlab.server import app

_BASE_URL = "http://testlab.test"


def test_static_generated_mount_serves_canvas_modules_without_shadowing_effects_static() -> None:
    """Verify /static/generated serves built canvas assets alongside /effects-static."""
    with TestClient(app) as client:
        image_generated = client.get("/static/generated/canvas/image-effects.js")
        assert image_generated.status_code == 200
        assert "attachImageEffect" in image_generated.text

        animation_generated = client.get("/static/generated/canvas/animation-effects.js")
        assert animation_generated.status_code == 200
        assert "layerTransform" in animation_generated.text

        vad_generated = client.get("/static/generated/canvas/testlab-audio-vad.js")
        assert vad_generated.status_code == 200
        assert "initAudioVadLab" in vad_generated.text

        splitting_vendor = client.get("/effects-static/js/splitting.min.js")
        assert splitting_vendor.status_code == 200


def test_lab_pages_resolve_migrated_canvas_modules_without_404() -> None:
    """Verify effects, animation-benchmark, layered-animation, and vad pages load modules cleanly."""
    expected_modules = [
        ("/effects", "/static/generated/canvas/image-effects.js"),
        ("/animation-benchmark", "/static/generated/canvas/animation-effects.js"),
        ("/layered-animation", "/static/generated/canvas/animation-effects.js"),
        ("/vad", "/static/generated/canvas/testlab-audio-vad.js"),
    ]
    with TestClient(app) as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for route_path, generated_module_path in expected_modules:
                page = browser.new_page()
                responses: list[tuple[str, int]] = []
                page_errors: list[str] = []

                def handle_route(route: Route) -> None:
                    url = route.request.url
                    if not url.startswith(_BASE_URL):
                        route.abort()
                        return
                    path = url[len(_BASE_URL):]
                    result = client.request(
                        route.request.method,
                        path,
                        content=route.request.post_data,
                    )
                    headers = dict(result.headers)
                    route.fulfill(
                        status=result.status_code,
                        headers=headers,
                        body=result.content,
                    )

                def on_response(response: Response) -> None:
                    responses.append((response.url, response.status))

                def on_page_error(error: Exception) -> None:
                    page_errors.append(str(error))

                def on_request_failed(request: Request) -> None:
                    if request.url.startswith(_BASE_URL):
                        page_errors.append(f"Request failed: {request.url}")

                page.route(f"{_BASE_URL}/**", handle_route)
                page.on("response", on_response)
                page.on("pageerror", on_page_error)
                page.on("requestfailed", on_request_failed)

                page.goto(f"{_BASE_URL}{route_path}", wait_until="networkidle")

                failed_statuses = [
                    (url, status) for url, status in responses if status >= 400
                ]
                assert failed_statuses == [], f"{route_path} returned HTTP errors: {failed_statuses}"
                assert page_errors == [], f"{route_path} raised page errors: {page_errors}"

                matching_generated = [
                    status
                    for url, status in responses
                    if url.startswith(f"{_BASE_URL}{generated_module_path}")
                ]
                assert matching_generated == [200], (
                    f"{route_path} did not load {generated_module_path} with 200: {responses}"
                )
                page.close()
        finally:
            browser.close()


def test_startup_builds_canvas_assets() -> None:
    with patch("testlab.server.ensure_canvas_assets_built") as build:
        with TestClient(app) as client:
            build.assert_called_once_with()
            response = client.get("/static/generated/canvas/game-renderer.js")
            assert response.status_code == 200
