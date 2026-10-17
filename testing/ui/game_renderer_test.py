from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest
from playwright.sync_api import Page, Route, expect, sync_playwright

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
        toggle.click()
        game.report_error('Game action failed; the previous game state was retained.')
        page.evaluate('window.refresh()')
        expect(toggle).to_have_attribute('data-game-error', 'true')
        expect(page.locator('#game-state-layer')).to_be_hidden()
        toggle.hover()
        expect(page.locator('#game-state-layer')).to_be_hidden()
        toggle.click()
        expect(page.get_by_role('alert')).to_contain_text('previous game state was retained')
        expect(page.locator('[data-game-id="counter"]')).to_have_text('Count: 1')
        page.get_by_role('button', name='Add one').click()
        expect(toggle).to_have_attribute('data-game-error', 'false')
        expect(page.get_by_role('alert')).to_have_count(0)
        browser.close()


@pytest.fixture
def movable_game_page() -> Iterator[Page]:
    game = GameState(Path("adventures/example_adventure"), Mock(), Mock())
    html = """<!doctype html><html><head></head><body style="background:#101827; margin:0; padding:0; width:1000px; height:800px; position:relative;">
    <button id="game-state-toggle-btn" hidden aria-label="Toggle game" aria-expanded="false"></button>
    <div id="game-state-layer" hidden></div><script type="module">
    import { createGameRenderer } from '/static/js/game-renderer.js';
    window.editable = true;
    window.moves = [];
    window.refresh = async () => renderer.render(await (await fetch('/latest')).json());
    const renderer = createGameRenderer({
        container: document.getElementById('game-state-layer'),
        toggleButton: document.getElementById('game-state-toggle-btn'),
        actionUrl: () => '/action',
        canEdit: () => window.editable,
        refresh: window.refresh,
        onMove: move => window.moves.push(move),
    });
    window.renderer = renderer;
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
        page = browser.new_page(viewport={"width": 1000, "height": 800})
        page.route("http://game.test/**", route_request)
        page.goto("http://game.test/")
        toggle = page.get_by_role("button", name="Toggle game")
        toggle.click()
        expect(page.locator(".game-panel")).to_be_visible()
        yield page
        browser.close()


@pytest.mark.parametrize("edge", ["top", "bottom", "left", "right", "control"])
def test_game_panel_drag_preserves_grab_offset_and_saves_position(movable_game_page: Page, edge: str) -> None:
    panel = movable_game_page.locator(".game-panel")
    bounds = panel.bounding_box()
    assert bounds is not None
    x = bounds["x"] + bounds["width"] / 2
    y = bounds["y"] + bounds["height"] / 2
    if edge == "top":
        y = bounds["y"] + 10
    elif edge == "bottom":
        y = bounds["y"] + bounds["height"] - 10
    elif edge == "left":
        x = bounds["x"] + 10
    elif edge == "right":
        x = bounds["x"] + bounds["width"] - 10
    else:
        control = movable_game_page.locator(".game-move").bounding_box()
        assert control is not None
        x = control["x"] + control["width"] / 2
        y = control["y"] + control["height"] / 2

    movable_game_page.mouse.move(x, y)
    movable_game_page.mouse.down()
    movable_game_page.mouse.move(x + 100, y + 80)
    assert panel.evaluate("el => el.style.left") == "60%"
    assert panel.evaluate("el => el.style.top") == "60%"
    assert "dragging" in panel.get_attribute("class").split()
    movable_game_page.mouse.up()
    assert "dragging" not in panel.get_attribute("class").split()
    assert movable_game_page.evaluate("window.moves") == [{"left_pct": 60, "top_pct": 60}]


def test_game_panel_button_click_does_not_drag(movable_game_page: Page) -> None:
    panel = movable_game_page.locator(".game-panel")
    button = movable_game_page.locator('[data-game-id="square-40"]')
    button.click()
    assert panel.evaluate("el => el.style.left") == "50%"
    assert panel.evaluate("el => el.style.top") == "50%"
    assert movable_game_page.evaluate("window.moves") == []
    expect(button).to_have_attribute("aria-pressed", "true")


def test_game_panel_cancelled_drag_restores_position(movable_game_page: Page) -> None:
    panel = movable_game_page.locator(".game-panel")
    movable_game_page.mouse.move(370, 400)
    movable_game_page.mouse.down()
    movable_game_page.mouse.move(470, 480)
    panel.evaluate("el => el.dispatchEvent(new PointerEvent('pointercancel', {pointerId: 1}))")
    movable_game_page.mouse.up()
    assert panel.evaluate("el => el.style.left") == "50%"
    assert panel.evaluate("el => el.style.top") == "50%"
    assert movable_game_page.evaluate("window.moves") == []


def test_game_panel_read_only_cannot_be_dragged(movable_game_page: Page) -> None:
    movable_game_page.evaluate("""() => {
        window.editable = false;
        window.refresh();
    }""")
    expect(movable_game_page.locator(".game-panel-editable")).to_have_count(0)
    expect(movable_game_page.locator(".game-move")).to_have_count(0)
    panel = movable_game_page.locator(".game-panel")
    movable_game_page.mouse.move(370, 400)
    movable_game_page.mouse.down()
    movable_game_page.mouse.move(470, 480)
    movable_game_page.mouse.up()
    assert panel.evaluate("el => el.style.left") == "50%"
    assert panel.evaluate("el => el.style.top") == "50%"
    assert movable_game_page.evaluate("window.moves") == []


def test_game_panel_toggle_preserves_position(movable_game_page: Page) -> None:
    control = movable_game_page.locator(".game-move").bounding_box()
    assert control is not None
    x = control["x"] + control["width"] / 2
    y = control["y"] + control["height"] / 2
    movable_game_page.mouse.move(x, y)
    movable_game_page.mouse.down()
    movable_game_page.mouse.move(x + 100, y + 80)
    movable_game_page.mouse.up()
    panel = movable_game_page.locator(".game-panel")
    assert panel.evaluate("el => el.style.left") == "60%"
    assert panel.evaluate("el => el.style.top") == "60%"


    toggle = movable_game_page.get_by_role("button", name="Toggle game")
    toggle.click()
    expect(panel).to_be_hidden()
    toggle.click()
    expect(panel).to_be_visible()
    assert panel.evaluate("el => el.style.left") == "60%"
    assert panel.evaluate("el => el.style.top") == "60%"

