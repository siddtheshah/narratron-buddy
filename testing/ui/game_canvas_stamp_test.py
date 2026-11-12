"""Click a real checkers victory and render its native stamp across refreshes."""
from pathlib import Path

from playwright.sync_api import Route, expect, sync_playwright

from game_engine.game_runtime import GameAction
from testing.game_fixtures import make_example_canvas

from testing.ui.canvas_source import read_ui_source


def test_winning_checkers_refreshes_the_page_and_keeps_crown_when_panel_closes(tmp_path: Path) -> None:
    canvas = make_example_canvas(tmp_path)
    game = canvas.game
    assert game is not None and game.program is not None
    game.load({"definition_hash": game.definition_hash, "revision": 0, "state": {
        **game.program.definition.initial, "board": [1 if i == 42 else -1 if i == 35 else 0 for i in range(64)],
    }})
    template = read_ui_source(Path("templates/canvas.html"))
    start = template.index("                gameRenderer.render(data.game_state);")
    refresh_output = template[start:template.index("                if (data.sticky_notes", start)]
    html = """<!doctype html><html><head><link rel="stylesheet" href="/static/css/chat.css"></head>
    <body style="margin:0;background:#1c2634;color:white">
    <div id="image-container" style="position:relative;width:1000px;height:700px">
      <canvas id="drawing" width="1000" height="700"></canvas>
      <div id="canvas-stamp-layer" style="position:absolute;inset:0"></div>
      <button id="toggle">Game</button><div id="game"></div>
    </div><script type="module">
    import {createGameRenderer} from '/static/js/game-renderer.js';
    import {createDoodleRenderer} from '/static/js/canvas-renderers.js';
    let lastGameCanvasRevision = null;
    const stamps = createDoodleRenderer({canvas:document.querySelector('#drawing'),
        stampLayer:document.querySelector('#canvas-stamp-layer'), canMoveStamp:()=>true});
    async function requestPageDoodles() {
        const snapshot = await (await fetch('/doodles')).json();
        stamps.redraw(snapshot.stamps);
    }
    async function refresh() {
        const data = await (await fetch('/latest')).json();
    """ + refresh_output + """
    }
    const gameRenderer = createGameRenderer({container:document.querySelector('#game'),
        toggleButton:document.querySelector('#toggle'), canEdit:()=>true,
        actionUrl:()=>'/action', refresh, initiallyOpen:true});
    refresh();
    </script></body></html>"""
    submitted: list[GameAction] = []

    def route_request(route: Route) -> None:
        path = route.request.url.split("http://game.test", 1)[-1]
        if path == "/":
            route.fulfill(content_type="text/html", body=html)
        elif path == "/latest":
            route.fulfill(json={"game_state": game.payload()})
        elif path == "/doodles":
            route.fulfill(json=canvas.doodles.snapshot())
        elif path == "/action":
            action = GameAction.model_validate_json(route.request.post_data or "{}")
            assert action.canvas_revision is not None
            submitted.append(action)
            route.fulfill(json=game.act(action))
        elif path.startswith("/static/"):
            route.fulfill(path=Path(path.split("?", 1)[0].lstrip("/")))
        else:
            asset = canvas.doodles.stamp_annotations()[0]
            assert path == asset["url"]
            relative = Path(str(asset["asset_path"])).relative_to(canvas.theater.output_dir()).as_posix()
            assert path == f"/theaters/checkers/output/{relative}"
            route.fulfill(path=str(asset["asset_path"]))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1100, "height": 800})
        page.route("http://game.test/**", route_request)
        page.goto("http://game.test/")
        frame = page.frame_locator("iframe.game-html-panel")
        frame.locator("#square-42").click()
        frame.locator("#square-28").click()
        expect(frame.locator("#status")).to_have_text("Red wins")
        crown = page.locator(".canvas-stamp-annotation")
        expect(crown).to_have_count(1)
        expect(crown.locator("img")).to_be_visible()
        assert page.evaluate("document.querySelector('.canvas-stamp-annotation img').naturalWidth") > 0
        assert game.state["winner"] == 1 and len(submitted) == 2
        page.locator("#toggle").click()
        expect(page.locator("#game")).to_be_hidden()
        expect(crown).to_be_visible()
        page.reload()
        expect(crown).to_have_count(1)
        expect(crown).to_be_visible()
        browser.close()
