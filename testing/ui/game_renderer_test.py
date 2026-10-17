"""Browser exercise of the actual renderer against the real transition engine."""

from pathlib import Path
from unittest.mock import Mock

from playwright.sync_api import Route, expect, sync_playwright

from components.canvas.game_state import GameAction, GameState
from testing.game_fixtures import make_game


def test_checkers_renderer_moves_and_viewer_permissions(tmp_path: Path) -> None:
    game = GameState(Path("adventures/example_adventure"), Mock(), Mock())
    html = """<!doctype html><html><head></head><body style="background:#101827">
    <button id="game-state-toggle-btn" hidden aria-label="Toggle game" aria-expanded="false"></button>
    <div id="game-state-layer" hidden></div><script type="module">
    import { createGameRenderer } from '/static/js/game-renderer.js';
    window.editable = true;
    window.refresh = async () => renderer.render(await (await fetch('/latest')).json());
    const renderer = createGameRenderer({container: document.getElementById('game-state-layer'),
        toggleButton: document.getElementById('game-state-toggle-btn'),
        actionUrl: () => '/action', canEdit: () => window.editable, refresh: window.refresh});
    window.refresh();
    </script></body></html>"""

    def route_request(route: Route) -> None:
        path = route.request.url.split("http://game.test", 1)[-1]
        if path == "/":
            route.fulfill(content_type="text/html", body=html)
        elif path == "/latest":
            route.fulfill(json=game.payload())
        elif path == "/action":
            action = GameAction.model_validate_json(route.request.post_data or "{}")
            route.fulfill(json=game.act(action))
        elif path.startswith("/static/"):
            route.fulfill(path=Path(path.split("?", 1)[0].lstrip("/")))
        else:
            route.fulfill(status=404)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        page.route("http://game.test/**", route_request)
        page.goto("http://game.test/")
        toggle = page.get_by_role("button", name="Toggle game")
        expect(toggle).to_be_visible()
        toggle.hover()
        expect(page.locator("#game-state-layer")).to_be_hidden()
        expect(toggle).to_have_attribute("aria-expanded", "false")
        toggle.click()
        expect(page.locator("#game-state-layer")).to_be_visible()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        expect(page.locator(".game-grid .game-button")).to_have_count(64)
        page.locator('[data-game-id="square-40"]').click()
        expect(page.locator('[data-game-id="square-33"]')).to_be_enabled()
        page.locator('[data-game-id="square-33"]').click()
        expect(page.locator('[data-game-id="status"]')).to_have_text("Ivory to move")
        assert game.state["board"][33] == 1
        toggle.click()
        page.evaluate("window.refresh()")
        expect(page.locator("#game-state-layer")).to_be_hidden()
        page.reload()
        expect(page.locator("#game-state-layer")).to_be_hidden()
        toggle.click()
        expect(page.locator('[data-game-id="square-33"] circle')).to_have_attribute("fill", "#e05252")
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(tmp_path / "checkers-mobile.png"))
        bounds = page.locator(".game-panel").bounding_box()
        assert bounds is not None and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 390
        page.evaluate("window.editable = false; window.refresh()")
        expect(page.get_by_role("button", name="New game")).to_be_disabled()
        assert page.locator(".game-grid button:enabled").count() == 0
        # A different state schema and SVG view work without another renderer.
        game = make_game(tmp_path / "counter")
        page.reload()
        toggle.click()
        expect(page.locator('[data-game-id="counter"]')).to_have_text("Count: 0")
        expect(page.locator(".game-svg circle")).to_have_attribute("r", "0")
        page.get_by_role("button", name="Add one").click()
        expect(page.locator('[data-game-id="counter"]')).to_have_text("Count: 1")
        assert game.state == {"count": 1}
        browser.close()
