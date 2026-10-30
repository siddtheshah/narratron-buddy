"""Exercise the complete OBS page with local assets and simulated state sockets."""

import json
from collections.abc import Iterator
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Page, Route, WebSocketRoute, expect, sync_playwright
import pytest

from testing.reference_images import png_bytes


@pytest.fixture
def obs_page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 1280, "height": 720}, reduced_motion="reduce")
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("""window.playedAudio = [];
            window.Audio = class extends Audio {
                play() {
                    window.playedAudio.push({src: this.src, volume: this.volume});
                    return Promise.resolve();
                }
            };
        """)

        def serve(route: Route) -> None:
            path = urlsplit(route.request.url).path
            if path.startswith("/static/"):
                route.fulfill(path=Path(path.lstrip("/")))
            elif path.startswith("/obs"):
                route.fulfill(content_type="text/html", path="templates/obs.html")
            elif path == "/api/latest":
                route.fulfill(json={"latest": "/image.png?variant=original", "time": 1,
                                    "effect": "none", "narration": "The curtain rises."})
            elif path in {"/image.png", "/favicon.png"}:
                route.fulfill(content_type="image/png", body=png_bytes(), headers={"Access-Control-Allow-Origin": "*"})
            elif path in {"/css2", "/forest.mp3", "/future.mp3"}:
                route.fulfill(body="")
            else:
                raise AssertionError("Unexpected OBS request: " + route.request.url)

        def socket(route: WebSocketRoute) -> None:
            if "/ws/canvas-state" in route.url:
                route.send('{"type":"state_ready"}')
            else:
                route.send('{"type":"doodle_snapshot","batches":[],"annotations":[],"stamps":[]}')

        page.route("**/*", serve)
        page.route_web_socket("**/ws/**", socket)
        yield page
        browser.close()
        assert not errors, "OBS JavaScript errors: " + "; ".join(errors)


@pytest.mark.parametrize("url", ["/obs?theater_id=test&join_key=JOIN", "/obs/test?join_key=JOIN"])
def test_obs_starts_and_renders_state(obs_page: Page, url: str) -> None:
    obs_page.goto("http://narratron.test" + url)
    expect(obs_page.locator(".scene-description")).to_have_text("The curtain rises.")
    expect(obs_page.locator("#current-image")).to_have_js_property("complete", True)
    expect(obs_page.locator("#current-image")).to_have_js_property("naturalWidth", 1)
    source = obs_page.locator("#current-image").get_attribute("src")
    assert source is not None
    assert "variant=original" in source
    assert "join_key=JOIN" in source


@pytest.mark.parametrize("image_origin", ["", "https://fal.media"])
def test_obs_renders_animation_without_still_image(obs_page: Page, image_origin: str) -> None:
    def animation_state(route: Route) -> None:
        route.fulfill(json={"time": 1, "effect": "none", "animation": {
            "type": "triframe", "frames": [image_origin + "/image.png?frame=1", image_origin + "/image.png?frame=2"],
            "frame_duration_ms": 10000,
        }})

    obs_page.route("**/api/latest?**", animation_state)
    obs_page.goto("http://narratron.test/obs/test?join_key=JOIN")
    expect(obs_page.locator("#current-image")).to_have_js_property("naturalWidth", 1)
    source = obs_page.locator("#current-image").get_attribute("src")
    assert source is not None
    assert "frame=1" in source
    if image_origin:
        assert source.startswith(image_origin)
        assert "join_key" not in source
    else:
        assert "join_key=JOIN" in source


@pytest.mark.parametrize(("query", "volume"), [("", 0.8), ("&audio_volume=0", 0), ("&audio_volume=0.3", 0.3)])
def test_obs_music_volume(obs_page: Page, query: str, volume: float) -> None:
    def music_state(route: Route) -> None:
        route.fulfill(json={"music": {"playlist": "forest", "tracks": ["/forest.mp3"], "time": 1}})

    obs_page.route("**/api/latest?**", music_state)
    obs_page.goto("http://narratron.test/obs/test?join_key=JOIN" + query)
    obs_page.wait_for_function("window.playedAudio.length > 0")
    assert obs_page.evaluate("window.playedAudio[0].volume") == volume
    assert "join_key=JOIN" in obs_page.evaluate("window.playedAudio[0].src")


def test_shared_scene_preserves_speech_until_scene_commit(obs_page: Page) -> None:
    obs_page.goto("http://narratron.test/obs/test")
    expect(obs_page.locator(".scene-description")).to_have_text("The curtain rises.")
    result = obs_page.evaluate("""async () => {
        const { createCanvasScene } = await import('/static/js/canvas-scene.js');
        const element = document.createElement('div');
        const scene = createCanvasScene({
            narrationPane: { element, prepare: caption => { caption.tabIndex = 0; } },
            resolveUrl: url => url + '?join_key=JOIN',
        });
        scene.enqueueSceneSpeech('/future.mp3', 2);
        scene.updateSceneDialogue([], 'First scene', null, 1);
        const beforeCommit = window.playedAudio.length;
        scene.updateSceneDialogue([{speaker: 'Guide', text: 'Welcome', kind: 'thought',
            spans: [{text: 'Welcome', color: 'red', font: 'fantasy'}]}], 'Next scene', null, 2);
        const afterCommit = window.playedAudio.length;
        scene.updateSceneDialogue([], 'Next scene', null, 2);
        scene.enqueueSceneSpeech('/old.mp3', 1);
        return { beforeCommit, afterCommit, finalCount: window.playedAudio.length,
            source: window.playedAudio[0].src,
            caption: element.querySelector('.scene-description').textContent,
            focusable: element.querySelector('.scene-description').tabIndex };
    }""")
    assert result == {"beforeCommit": 0, "afterCommit": 1, "finalCount": 1,
                      "source": "http://narratron.test/future.mp3?join_key=JOIN",
                      "caption": "Next scene", "focusable": 0}



def test_obs_restores_doodles_from_selected_history_page(obs_page: Page) -> None:
    selected = [0]
    sockets: list[WebSocketRoute] = []

    def snapshot(page_id: str) -> dict[str, str | bool | list[dict[str, str | int | list[float]]]]:
        return {"type": "doodle_snapshot", "page_id": page_id, "enabled": True,
                "batches": [{"points": [0.1, 0.1, 0.8, 0.8], "color": "#ff0000", "size": 10}] if page_id == "first" else []}

    def history_state(route: Route) -> None:
        route.fulfill(json={"latest": "/image.png", "time": selected[0] + 1, "effect": "none",
                            "doodle_page_id": ["first", "second"][selected[0]],
                            "orator_cursor": selected[0], "doodles_enabled": True,
                            "history": [{"page_id": "first"}, {"page_id": "second"}]})

    def doodle_state(route: Route) -> None:
        route.fulfill(json=snapshot(parse_qs(urlsplit(route.request.url).query)["page_id"][0]))

    def doodle_socket(route: WebSocketRoute) -> None:
        def receive(message: str | bytes) -> None:
            data = json.loads(message)
            route.send(json.dumps(snapshot(data["page_id"])))
        route.on_message(receive)
        route.send(json.dumps(snapshot("first")))

    def state_socket(route: WebSocketRoute) -> None:
        sockets.append(route)
        route.send('{"type":"state_ready"}')

    obs_page.route("**/api/latest?**", history_state)
    obs_page.route("**/api/doodles?**", doodle_state)
    obs_page.route_web_socket("**/ws/doodle?**", doodle_socket)
    obs_page.route_web_socket("**/ws/canvas-state?**", state_socket)
    obs_page.goto("http://narratron.test/obs/test")
    expect(obs_page.locator("#current-image")).to_have_js_property("naturalWidth", 1)
    first_drawing = obs_page.locator("#doodle-canvas").evaluate("canvas => canvas.toDataURL()")
    selected[0] = 1
    sockets[0].send('{"type":"state_changed","domains":["latest"]}')
    obs_page.wait_for_function("previous => document.getElementById('doodle-canvas').toDataURL() !== previous", arg=first_drawing)
    selected[0] = 0
    sockets[0].send('{"type":"state_changed","domains":["latest"]}')
    obs_page.wait_for_function("expected => document.getElementById('doodle-canvas').toDataURL() === expected", arg=first_drawing)
