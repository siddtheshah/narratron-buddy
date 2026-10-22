from collections.abc import Iterator
from pathlib import Path
from unittest.mock import Mock

import pytest
from playwright.sync_api import Error, Page, Request, Route, expect, sync_playwright

from components.canvas.game_runtime import ViewNode
from components.canvas.game_state import GameAction, GameState
from testing.game_fixtures import counter_definition, make_game


def test_canvas_game_permissions_do_not_interrupt_startup() -> None:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    start = template.index("        const gameRenderer = createGameRenderer({")
    renderer_setup = template[start:template.index("        const a2uiCanvasRenderer", start)]
    start = template.index("        function isCollabModeEnabled()")
    permission_helpers = template[start:template.index("        // Beyond20", start)]
    # Keep the real canvas declaration order, including the initial viewer render.
    startup = [
        (template.index(declaration), declaration)
        for declaration in (
            "let currentUser = null;", "let viewerCollabEnabled = false;",
            "let currentBatonState = null;",
        )
    ]
    startup.extend([
        (template.index(renderer_setup), renderer_setup),
        (template.index("        applyRoleUI(false);"), "gameRenderer.render();"),
    ])
    script = "\n".join(snippet for _, snippet in sorted(startup))
    html = """<!doctype html><html><body>
    <button id="game-state-toggle-btn" hidden></button><div id="game-state-layer" hidden></div>
    <script type="module">
    import { createGameRenderer } from '/static/js/game-renderer.js';
    const isCurrentOrator = () => Boolean(window._isActiveOratorState);
    const fetchLatestImage = () => {};
    """ + permission_helpers + script + """
    window.canvasInitialized = true;
    </script></body></html>"""

    def route_request(route: Route) -> None:
        path = route.request.url.split("http://game.test", 1)[-1]
        if path == "/":
            route.fulfill(content_type="text/html", body=html)
        elif path.startswith("/static/"):
            route.fulfill(path=Path(path.split("?", 1)[0].lstrip("/")))
        else:
            route.fulfill(status=404)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        errors: list[str] = []

        def record_error(error: Error) -> None:
            errors.append(str(error))

        page.on("pageerror", record_error)
        page.route("http://game.test/**", route_request)
        page.goto("http://game.test/")
        assert errors == []
        assert page.evaluate("window.canvasInitialized") is True
        browser.close()


def test_checkers_renderer_moves_and_viewer_permissions(tmp_path: Path) -> None:
    game = GameState(Path("adventures/example_adventure"), Mock(), Mock())
    html = """<!doctype html><html><head></head><body style="background:#101827">
    <button id="game-state-toggle-btn" hidden aria-label="Toggle game" aria-expanded="false"></button>
    <div id="game-state-layer" hidden></div><script type="module">
    import { createGameRenderer } from '/static/js/game-renderer.js';
    window.editable = true;
    window.contributable = false;
    window.refresh = async () => {
        window.lastGame = await (await fetch('/latest')).json();
        renderer.render(window.lastGame);
    };
    const renderer = createGameRenderer({container: document.getElementById('game-state-layer'),
        toggleButton: document.getElementById('game-state-toggle-btn'),
        actionUrl: () => '/action', canEdit: () => window.editable,
        canContribute: () => window.contributable, refresh: window.refresh});
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
        expect(page.locator('[data-game-id="club-crest"]')).to_have_attribute("data-game-svg", "crest.svg")
        expect(page.locator(".game-grid .game-button")).to_have_count(64)
        page.locator('[data-game-id="square-40"]').click()
        expect(page.locator('[data-game-id="square-33"]')).to_be_enabled()
        page.locator('[data-game-id="square-33"]').click()
        expect(page.locator('[data-game-id="status"]')).to_have_text("Ivory to move")
        assert game.revision == 0
        page.get_by_role('button', name='Submit', exact=True).click()
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
        # When collaboration is enabled for a contributor, contributor actions are enabled while orator-only actions stay disabled.
        page.evaluate("window.contributable = true; window.refresh()")
        expect(page.get_by_role("button", name="New game")).to_be_disabled()
        expect(page.locator('[data-game-id="square-17"]')).to_be_enabled()
        page.locator('[data-game-id="square-17"]').click()
        expect(page.locator('[data-game-id="square-26"]')).to_be_enabled()
        page.locator('[data-game-id="square-26"]').click()
        expect(page.locator('[data-game-id="status"]')).to_have_text("Red to move")
        page.get_by_role('button', name='Submit', exact=True).click()
        assert game.state["board"][26] == -1
        # A different state schema and SVG view (including game/*.svg asset) work without another renderer.
        counter_dir = tmp_path / "counter"
        (counter_dir / "game").mkdir(parents=True, exist_ok=True)
        (counter_dir / "game/badge.svg").write_text(
            '<svg viewBox="0 0 24 24"><g fill="#facc15"><path d="M12 2 L15 9 L22 9 L16 14 L18 21 L12 17 L6 21 L8 14 L2 9 L9 9 Z"/></g></svg>',
            encoding="utf-8",
        )
        definition = counter_definition()
        definition.view.children.append(ViewNode(kind="svg", props={"src": '"badge.svg"', "id": '"svg-badge"'}))
        game = make_game(counter_dir, definition)
        page.reload()
        toggle.click()
        expect(page.locator('[data-game-id="counter"]')).to_have_text("Count: 0")
        expect(page.locator(".game-svg circle")).to_have_attribute("r", "0")
        expect(page.locator('[data-game-id="svg-badge"]')).to_have_attribute("data-game-svg", "badge.svg")
        expect(page.locator('[data-game-id="svg-badge"] g path')).to_have_attribute(
            "d", "M12 2 L15 9 L22 9 L16 14 L18 21 L12 17 L6 21 L8 14 L2 9 L9 9 Z"
        )
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
    window.contributable = false;
    window.movable = false;
    window.moves = [];
    window.refresh = async () => {
        window.lastGame = await (await fetch('/latest')).json();
        renderer.render(window.lastGame);
    };
    const renderer = createGameRenderer({
        container: document.getElementById('game-state-layer'),
        toggleButton: document.getElementById('game-state-toggle-btn'),
        actionUrl: () => '/action',
        canEdit: () => window.editable,
        canContribute: () => window.contributable,
        canMove: () => window.editable || window.movable,
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


@pytest.mark.parametrize("collaboration", [False, True])
def test_contributor_panel_position_is_private_and_survives_refresh(
    movable_game_page: Page, collaboration: bool,
) -> None:
    page = movable_game_page
    page.evaluate("""collaboration => {
        window.editable = false;
        window.movable = true;
        window.contributable = collaboration;
        window.renderer.render({...window.lastGame, placement: {left_pct: 50, top_pct: 50}});
    }""", collaboration)
    control = page.locator(".game-move")
    expect(control).to_be_visible()
    bounds = control.bounding_box()
    assert bounds is not None
    x = bounds["x"] + bounds["width"] / 2
    y = bounds["y"] + bounds["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 100, y + 80)
    page.mouse.up()
    panel = page.locator(".game-panel")
    assert panel.evaluate("el => el.style.left") == "60%"
    assert panel.evaluate("el => el.style.top") == "60%"
    assert page.evaluate("window.moves") == []
    page.evaluate("""window.renderer.render({...window.lastGame, revision: 1,
        placement: {left_pct: 40, top_pct: 40}})""")
    assert panel.evaluate("el => el.style.left") == "60%"
    assert panel.evaluate("el => el.style.top") == "60%"
    toggle = page.get_by_role("button", name="Toggle game")
    toggle.click()
    toggle.click()
    assert panel.evaluate("el => el.style.left") == "60%"
    assert panel.evaluate("el => el.style.top") == "60%"
    expect(page.get_by_role("button", name="New game")).to_be_disabled()


def test_exploration_undo_and_polling_need_no_action_requests(movable_game_page: Page) -> None:
    page = movable_game_page
    requests: list[str] = []

    def record_request(request: Request) -> None:
        requests.append(request.url)

    page.on('request', record_request)
    page.locator('[data-game-id="square-40"]').click()
    submit = page.get_by_role('button', name='Submit', exact=True)
    expect(submit).to_be_disabled()
    page.locator('[data-game-id="square-33"]').click()
    expect(submit).to_be_enabled()
    page.mouse.move(0, 0)
    assert submit.evaluate('el => getComputedStyle(el).backgroundColor') == 'rgb(249, 115, 22)'
    expect(page.locator('[data-game-id="status"]')).to_have_text('Ivory to move')
    assert requests == []
    page.get_by_role('button', name='Undo', exact=True).click()
    expect(submit).to_be_disabled()
    assert submit.evaluate('el => getComputedStyle(el).backgroundColor') != 'rgb(249, 115, 22)'
    expect(page.locator('[data-game-id="square-40"]')).to_have_attribute('aria-pressed', 'true')
    page.locator('[data-game-id="square-42"]').click()
    page.locator('[data-game-id="square-35"]').click()
    assert requests == []
    page.evaluate('window.refresh()')
    expect(page.locator('[data-game-id="square-35"] circle')).to_have_attribute('fill', '#e05252')
    assert len(requests) == 1 and requests[0].endswith('/latest')
    page.get_by_role('button', name='Submit', exact=True).click()
    expect(page.get_by_role('button', name='Submit', exact=True)).to_have_count(0)
    assert len([url for url in requests if url.endswith('/action')]) == 1
    page.wait_for_function('window.lastGame.revision === 1')
    assert page.evaluate('window.lastGame.revision') == 1
    assert page.evaluate('window.lastGame.state.board[40]') == 1
    assert page.evaluate('window.lastGame.state.board[35]') == 1


def test_information_tabs_are_private_accessible_and_survive_updates(movable_game_page: Page) -> None:
    page = movable_game_page
    page.evaluate('''() => {
        window.editable = false;
        window.lastGame = {...window.lastGame, view: [{kind: 'tabs',
            props: {id: 'information', label: 'Game information'}, children: [
                {kind: 'column', props: {label: 'Events'}, children: [
                    {kind: 'text', props: {text: 'Latest event'}}]},
                {kind: 'column', props: {label: 'Terms'}, children: [
                    {kind: 'text', props: {text: 'Term definitions'}}]},
            ]}]};
        window.renderer.render(window.lastGame);
    }''')
    requests: list[str] = []

    def record_request(request: Request) -> None:
        requests.append(request.url)

    page.on('request', record_request)
    events = page.get_by_role('tab', name='Events', exact=True)
    terms = page.get_by_role('tab', name='Terms', exact=True)
    expect(events).to_have_attribute('aria-selected', 'true')
    expect(page.get_by_role('tabpanel', name='Events')).to_be_visible()
    expect(page.get_by_role('tabpanel', name='Terms', include_hidden=True)).to_be_hidden()
    events.focus()
    events.press('ArrowLeft')
    expect(terms).to_be_focused()
    expect(terms).to_have_attribute('tabindex', '0')
    expect(events).to_have_attribute('tabindex', '-1')
    expect(page.get_by_role('tabpanel', name='Terms')).to_be_visible()
    terms.press('Home')
    expect(events).to_be_focused()
    events.press('End')
    expect(terms).to_be_focused()
    terms.press('ArrowRight')
    expect(events).to_be_focused()
    terms.click()
    page.evaluate('window.renderer.render({...window.lastGame, revision: 1})')
    expect(terms).to_have_attribute('aria-selected', 'true')
    toggle = page.get_by_role('button', name='Toggle game')
    toggle.click()
    toggle.click()
    expect(terms).to_have_attribute('aria-selected', 'true')
    page.evaluate('window.renderer.render({...window.lastGame, definition_hash: "new-package"})')
    expect(events).to_have_attribute('aria-selected', 'true')
    assert requests == []


@pytest.mark.parametrize('change', ['revision', 'permission', 'package'])
def test_exploration_is_discarded_when_authority_changes(movable_game_page: Page, change: str) -> None:
    page = movable_game_page
    page.locator('[data-game-id="square-40"]').click()
    page.locator('[data-game-id="square-33"]').click()
    expect(page.get_by_role('button', name='Submit', exact=True)).to_be_visible()
    if change == 'revision':
        page.evaluate('window.renderer.render({...window.lastGame, revision: 1})')
    elif change == 'permission':
        page.evaluate('window.editable = false; window.renderer.render(window.lastGame)')
    else:
        page.evaluate('window.renderer.render({...window.lastGame, definition_hash: "new-package"})')
    expect(page.get_by_role('button', name='Submit', exact=True)).to_have_count(0)
    expect(page.locator('[data-game-id="status"]')).to_have_text('Red to move')
    expect(page.get_by_role('alert')).to_contain_text('Local exploration was discarded')
