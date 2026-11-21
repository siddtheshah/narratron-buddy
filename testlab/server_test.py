"""Tests for Test Lab static mounts and ES module loading."""

from collections.abc import Callable
from unittest.mock import patch

from fastapi.testclient import TestClient
from playwright.sync_api import Request, Response, Route, sync_playwright

from testlab.server import app

_BASE_URL = "http://testlab.test"


def _make_testlab_route_handler(client: TestClient) -> Callable[[Route], None]:
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
        route.fulfill(
            status=result.status_code,
            headers=dict(result.headers),
            body=result.content,
        )

    return handle_route


def test_static_generated_mount_serves_canvas_modules_without_shadowing_effects_static() -> None:
    """Verify /static/generated serves built canvas assets alongside /effects-static."""
    with TestClient(app) as client:
        image_generated = client.get("/static/generated/shared/image-effects.js")
        assert image_generated.status_code == 200
        assert image_generated.headers["content-type"].split(";")[0] in {"text/javascript", "application/javascript"}

        animation_generated = client.get("/static/generated/shared/animation-effects.js")
        assert animation_generated.status_code == 200
        assert animation_generated.headers["content-type"].split(";")[0] in {"text/javascript", "application/javascript"}

        vad_generated = client.get("/static/generated/testlab/testlab-audio-vad.js")
        assert vad_generated.status_code == 200
        assert vad_generated.headers["content-type"].split(";")[0] in {"text/javascript", "application/javascript"}

        splitting_vendor = client.get("/effects-static/js/splitting.min.js")
        assert splitting_vendor.status_code == 200


def test_lab_pages_resolve_migrated_canvas_modules_without_404() -> None:
    """Verify effects, animation-benchmark, layered-animation, and vad pages load modules cleanly."""
    expected_modules = [
        ("/effects", "/static/generated/shared/image-effects.js"),
        ("/animation-benchmark", "/static/generated/shared/animation-effects.js"),
        ("/layered-animation", "/static/generated/shared/animation-effects.js"),
        ("/vad", "/static/generated/testlab/testlab-audio-vad.js"),
    ]
    with TestClient(app) as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for route_path, generated_module_path in expected_modules:
                page = browser.new_page()
                responses: list[tuple[str, int]] = []
                page_errors: list[str] = []

                def on_response(response: Response) -> None:
                    responses.append((response.url, response.status))

                def on_page_error(error: Exception) -> None:
                    page_errors.append(str(error))

                def on_request_failed(request: Request) -> None:
                    if request.url.startswith(_BASE_URL):
                        page_errors.append(f"Request failed: {request.url}")

                page.route(f"{_BASE_URL}/**", _make_testlab_route_handler(client))
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
            response = client.get("/static/generated/game/game-renderer.js")
            assert response.status_code == 200


def test_avatar_lab_serves_and_triggers_narratron_actions() -> None:
    """Verify /avatar-lab?avatar=narratron loads Narratron assets and triggers action buttons."""
    with TestClient(app) as client, sync_playwright() as playwright:
        manifest_resp = client.get("/avatar-assets/narratron/avatar.json")
        assert manifest_resp.status_code == 200
        browser = playwright.chromium.launch(
            headless=True,
            args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
        )
        try:
            page = browser.new_page()
            page_errors: list[str] = []

            page.route(f"{_BASE_URL}/**", _make_testlab_route_handler(client))
            page.on("pageerror", lambda err: page_errors.append(str(err)))
            page.goto(f"{_BASE_URL}/avatar-lab?avatar=narratron", wait_until="networkidle")

            canvas = page.locator("#avatar canvas")
            canvas.wait_for(state="visible", timeout=10000)
            assert page.locator("#avatar-choice").input_value() == "narratron"

            labels = page.locator("#action-list .action .action-name").all_text_contents()
            assert labels == ["Idle", "Greeting", "Amused", "Surprised", "Confused", "At Work"]

            page.locator("#action-list .action", has_text="At Work").click()
            assert page.locator("#playback-status").text_content() == "Looping At Work"
            assert page.locator("#action-list .action", has_text="At Work").get_attribute("aria-pressed") == "true"

            page.locator("#action-list .action", has_text="Greeting").click()
            assert page.locator("#playback-status").text_content() == "Playing Greeting"
            assert page_errors == []
            page.close()
        finally:
            browser.close()


def test_avatar_lab_serves_and_triggers_umbral_sigil_actions() -> None:
    """Verify /avatar-lab?avatar=umbral-sigil loads Umbral Sigil assets and triggers actions."""
    with TestClient(app) as client, sync_playwright() as playwright:
        manifest_resp = client.get("/avatar-assets/umbral-sigil/avatar.json")
        assert manifest_resp.status_code == 200
        assert manifest_resp.headers.get("cache-control") == "no-cache"
        model_resp = client.get("/avatar-assets/umbral-sigil/model.glb")
        assert model_resp.status_code == 200
        assert model_resp.headers.get("cache-control") == "no-cache"
        browser = playwright.chromium.launch(
            headless=True,
            args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
        )
        try:
            page = browser.new_page()
            page_errors: list[str] = []

            page.route(f"{_BASE_URL}/**", _make_testlab_route_handler(client))
            page.on("pageerror", lambda err: page_errors.append(str(err)))
            page.goto(f"{_BASE_URL}/avatar-lab?avatar=umbral-sigil", wait_until="networkidle")

            canvas = page.locator("#avatar canvas")
            canvas.wait_for(state="visible", timeout=10000)
            assert page.locator("#avatar-choice").input_value() == "umbral-sigil"

            labels = page.locator("#action-list .action .action-name").all_text_contents()
            assert labels == [
                "Dormant Watch",
                "Narrowing Gaze",
                "Iron Bristle",
                "Sudden Frenzy",
                "Shattered Ward",
                "Iron Crown",
                "Spiteful Snap",
            ]

            page.locator("#action-list .action", has_text="Shattered Ward").click()
            assert page.locator("#playback-status").text_content() == "Looping Shattered Ward"
            assert (
                page.locator("#action-list .action", has_text="Shattered Ward").get_attribute(
                    "aria-pressed"
                )
                == "true"
            )

            page.locator("#action-list .action", has_text="Sudden Frenzy").click()
            assert page.locator("#playback-status").text_content() == "Playing Sudden Frenzy"
            assert page_errors == []
            page.close()
        finally:
            browser.close()
