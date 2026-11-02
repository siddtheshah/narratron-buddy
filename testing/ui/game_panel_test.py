from pathlib import Path
from unittest.mock import Mock

from playwright.sync_api import Route, expect, sync_playwright

from game_engine.game_runtime import GameAction
from game_engine.game_state import GameState
from testing.game_fixtures import panel_definition


def test_html_panel_binding_actions_permissions_and_network_isolation(tmp_path: Path) -> None:
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    definition = panel_definition()
    definition.constants["unsafe"] = '</script><script>parent.compromised = true</script><img src="https://evil.test/injected">'
    assert definition.panel is not None
    definition.panel.parameters["unsafe"] = "constants.unsafe"
    # Revalidate to regenerate the internal view from the panel contract.
    (game_dir / "game.json").write_text(definition.model_dump_json(exclude={"view"}), encoding="utf-8")
    (game_dir / "panel.html").write_text('''<!doctype html><html><head><style>
        @import "https://evil.test/import.css";
        p { background-image: url(https://evil.test/background); font-family: remote; }
        @font-face {font-family: remote; src: url(https://evil.test/font);}
        </style></head><body>
        <p data-panel-text="count"></p><pre data-panel-text="unsafe"></pre>
        <button data-panel-action="increment" data-panel-disabled="full">Add one</button>
        <button data-panel-action="unknown">Unknown action</button>
        </body></html>''', encoding="utf-8")
    game = GameState(tmp_path, Mock(), Mock(), backend="python")
    assert not game.error
    html = '''<!doctype html><button id="toggle"></button><div id="root"></div>
        <script type="module">
        import {createGameRenderer} from '/static/js/game-renderer.js';
        window.editable = true;
        window.renderer = createGameRenderer({container: document.querySelector('#root'),
            toggleButton: document.querySelector('#toggle'), actionUrl: () => '/action',
            canEdit: () => window.editable, initiallyOpen: true, refresh: () => {}});
        renderer.render(await (await fetch('/latest')).json());
        </script>'''

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
            raise AssertionError(f"Unexpected request: {route.request.url}")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        # All other traffic is rejected by the shared offline-browser fixture.
        page.route("http://game.test/**", route_request)
        page.goto("http://game.test/")
        frame = page.frame_locator("iframe.game-html-panel")
        expect(frame.locator("p")).to_have_text("0")
        expect(frame.locator("pre")).to_have_text(definition.constants["unsafe"])
        assert page.evaluate("window.compromised === undefined")
        expect(frame.get_by_role("button", name="Unknown action")).to_be_disabled()
        assert frame.locator("body").evaluate("() => {try {return parent.document.body !== null} catch {return false}}") is False
        # Messages from any other window cannot trigger a named action.
        page.evaluate("window.postMessage({type:'game-panel-action', id:'increment'}, '*')")
        expect(frame.locator("p")).to_have_text("0")
        frame.get_by_role("button", name="Add one").click()
        expect(frame.locator("p")).to_have_text("1")
        assert game.state == {"count": 1}
        page.evaluate("window.editable = false; renderer.render()")
        expect(frame.get_by_role("button", name="Add one")).to_be_disabled()
        browser.close()
