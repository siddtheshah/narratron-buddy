from pathlib import Path
from unittest.mock import Mock

from playwright.sync_api import Route, expect, sync_playwright

from game_engine.game_runtime import GameAction
from game_engine.game_state import GameState
from testing.game_fixtures import panel_definition


def test_html_panel_binding_actions_permissions_and_network_isolation(tmp_path: Path) -> None:
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    (game_dir / "pieces").mkdir()
    (game_dir / "pieces/token.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32"><circle cx="16" cy="16" r="12" fill="red" /></svg>',
        encoding="utf-8",
    )
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
        <img src="pieces/token.svg" alt="Token" width="32" height="32">
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
        expect(frame.get_by_role("img", name="Token")).to_be_visible()
        expect(frame.get_by_role("img", name="Token")).to_have_js_property("naturalWidth", 32)
        assert page.evaluate("window.compromised === undefined")
        expect(frame.get_by_role("button", name="Unknown action")).to_be_disabled()
        assert frame.locator("body").evaluate("() => {try {return parent.document.body !== null} catch {return false}}") is False
        # Messages from any other window cannot trigger a named action.
        page.evaluate("window.postMessage({type:'game-panel-action', id:'increment'}, '*')")
        expect(frame.locator("p")).to_have_text("0")
        frame.get_by_role("button", name="Add one").click()
        expect(frame.locator("p")).to_have_text("1")
        expect(frame.get_by_role("img", name="Token")).to_have_js_property("naturalWidth", 32)
        assert game.state == {"count": 1}
        page.evaluate("window.editable = false; renderer.render()")
        expect(frame.get_by_role("button", name="Add one")).to_be_disabled()

        # Default sizing (no author width/height specified): 510px panel width, 430px iframe height
        page.set_viewport_size({"width": 1280, "height": 1000})
        panel_box = page.locator(".game-panel").bounding_box()
        frame_box = page.locator("iframe.game-html-panel").bounding_box()
        assert panel_box is not None and frame_box is not None
        assert round(panel_box["width"]) == 510
        assert round(frame_box["height"]) == 430

        # Author-controlled sizing within limits (e.g. 620px width, 540px height)
        page.evaluate("""() => {
            window.renderer.render({
                definition_hash: 'custom-size',
                revision: 2,
                state: {count: 1},
                panel_html: '<!doctype html><html><head><style>body { width: 620px; height: 540px; }</style></head><body><p data-panel-text="count"></p></body></html>',
                view: [{kind: 'column', props: {count: 1}, children: []}],
            });
        }""")
        expect(frame.locator("p")).to_have_text("1")
        custom_panel = page.locator(".game-panel").bounding_box()
        custom_frame = page.locator("iframe.game-html-panel").bounding_box()
        assert custom_panel is not None and custom_frame is not None
        assert round(custom_panel["width"]) == 620
        assert round(custom_frame["height"]) == 540

        # Extreme author sizes are clamped to host bounds (max 960px width / 780px height, min 280px width / 220px height)
        page.evaluate("""() => {
            window.renderer.render({
                definition_hash: 'oversized',
                revision: 3,
                state: {count: 1},
                panel_html: '<!doctype html><html><head><style>body { width: 2400px; height: 2000px; }</style></head><body><p data-panel-text="count"></p></body></html>',
                view: [{kind: 'column', props: {count: 1}, children: []}],
            });
        }""")
        expect(frame.locator("p")).to_have_text("1")
        oversized_panel = page.locator(".game-panel").bounding_box()
        oversized_frame = page.locator("iframe.game-html-panel").bounding_box()
        assert oversized_panel is not None and oversized_frame is not None
        assert round(oversized_panel["width"]) == 960
        assert round(oversized_frame["height"]) == 780

        page.evaluate("""() => {
            window.renderer.render({
                definition_hash: 'undersized',
                revision: 4,
                state: {count: 1},
                panel_html: '<!doctype html><html><head><style>body { width: 80px; height: 60px; }</style></head><body><p data-panel-text="count"></p></body></html>',
                view: [{kind: 'column', props: {count: 1}, children: []}],
            });
        }""")
        expect(frame.locator("p")).to_have_text("1")
        undersized_panel = page.locator(".game-panel").bounding_box()
        undersized_frame = page.locator("iframe.game-html-panel").bounding_box()
        assert undersized_panel is not None and undersized_frame is not None
        assert round(undersized_panel["width"]) == 280
        assert round(undersized_frame["height"]) == 220
        browser.close()


def test_canvas_hotkeys_work_after_clicking_game_panel(tmp_path: Path) -> None:
    """Clicking inside a sandboxed HTML game panel must not swallow canvas mic/text hotkeys."""
    definition = panel_definition()
    (tmp_path / "game").mkdir()
    (tmp_path / "game/game.json").write_text(definition.model_dump_json(exclude={"view"}), encoding="utf-8")
    (tmp_path / "game/panel.html").write_text(
        '''<!doctype html><html><body>
        <p id="status" data-panel-text="count"></p>
        <button data-panel-action="increment" data-panel-disabled="full">Add one</button>
        <button data-panel-action="unknown">Disabled action</button>
        </body></html>''',
        encoding="utf-8",
    )
    game = GameState(tmp_path, Mock(), Mock(), backend="python")
    assert not game.error

    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    space_guard = template[
        template.index("        // Plain Space is reserved"):
        template.index("        function escapeHtml", template.index("        // Plain Space is reserved"))
    ]
    command_setup = template[
        template.index("        const oratorCommandOverlay = document.getElementById('orator-command-overlay');"):
        template.index("        const suggestionsHiddenStorageKey =", template.index("        const oratorCommandOverlay"))
    ]
    hotkey_setup = template[
        template.index("        // Configurable Mic Hotkey Logic"):
        template.index("        window.setMicHotkey =", template.index("        // Configurable Mic Hotkey Logic"))
    ]
    hotkey_setup = hotkey_setup[hotkey_setup.index("        const micHotkeyBtn = null;"):]

    html = f'''<!doctype html><html><body>
    <button id="agent-start-stop-btn" type="button">Banish</button>
    <button id="agent-connect-btn" type="button">Mic On</button>
    <button id="game-state-toggle-btn" type="button"></button>
    <div id="game-state-layer"></div>
    <div id="orator-command-overlay" aria-hidden="true">
        <form id="orator-command-form">
            <input id="orator-command-input" type="text">
            <span id="orator-command-dismiss-hotkey"></span>
            <span id="orator-command-status"></span>
        </form>
    </div>
    <script type="module">
    import {{ createGameRenderer }} from '/static/js/game-renderer.js';
    const theaterId = 'test';
    const isCurrentOrator = () => true;
    const stopSceneSpeech = () => {{}};
    const actionWheel = {{ cancelRebinding: () => {{}} }};
    const agentStartStopBtn = document.getElementById('agent-start-stop-btn');
    const agentConnectBtn = document.getElementById('agent-connect-btn');
    window.micMode = 'vad';
    let micMode = 'vad';
    let micPressHeld = false;
    let micPressSource = null;
    window.micClicks = 0;
    window.micPressLog = [];
    agentConnectBtn.addEventListener('click', () => {{ window.micClicks += 1; }});
    const beginMicPress = source => {{
        micPressHeld = true;
        micPressSource = source;
        window.micPressLog.push('begin:' + source);
    }};
    const releaseMicPress = () => {{
        micPressHeld = false;
        micPressSource = null;
        window.micPressLog.push('release');
    }};
    window.setMicMode = mode => {{ micMode = mode; window.micMode = mode; }};
    {space_guard}
    {command_setup}
    {hotkey_setup}
    const renderer = createGameRenderer({{
        container: document.getElementById('game-state-layer'),
        toggleButton: document.getElementById('game-state-toggle-btn'),
        actionUrl: () => '/action',
        canEdit: () => true,
        initiallyOpen: true,
        refresh: () => {{}},
    }});
    renderer.render(await (await fetch('/latest')).json());
    </script></body></html>'''

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
        page.route("http://game.test/**", route_request)
        page.goto("http://game.test/")
        frame = page.frame_locator("iframe.game-html-panel")
        expect(frame.locator("#status")).to_have_text("0")

        # Clicking inside the sandboxed game panel moves focus into the iframe.
        frame.locator("#status").click()
        assert page.evaluate("document.activeElement?.className") == "game-html-panel"

        # Spoofed key messages from any other window must be ignored.
        page.evaluate("window.postMessage({type:'game-panel-key', eventType:'keydown', key:' ', code:'Space', shiftKey:true}, '*')")
        assert page.evaluate("document.getElementById('orator-command-overlay').classList.contains('active')") is False

        # Text hotkey (Shift+Space) still opens and focuses the Orator command input.
        page.keyboard.press("Shift+Space")
        page.wait_for_function("document.getElementById('orator-command-overlay').classList.contains('active')")
        page.wait_for_function("document.activeElement?.id === 'orator-command-input'")
        page.keyboard.type("look around")
        expect(page.locator("#orator-command-input")).to_have_value("look around")
        page.keyboard.press("Escape")
        page.wait_for_function("!document.getElementById('orator-command-overlay').classList.contains('active')")

        # Click inside the iframe again (e.g. a disabled button) and trigger the mic hotkey (Ctrl+Shift+[).
        frame.get_by_role("button", name="Disabled action").click(force=True)
        assert page.evaluate("document.activeElement?.className") == "game-html-panel"
        page.keyboard.press("Control+Shift+BracketLeft")
        page.wait_for_function("window.micClicks === 1")

        # Push-to-talk keydown and keyup must both forward from the iframe.
        page.evaluate("window.setMicMode('push-to-talk')")
        frame.locator("#status").click()
        page.keyboard.down("Control")
        page.keyboard.down("Shift")
        page.keyboard.down("BracketLeft")
        page.wait_for_function("window.micPressLog.includes('begin:hotkey')")
        page.keyboard.up("BracketLeft")
        page.keyboard.up("Shift")
        page.keyboard.up("Control")
        page.wait_for_function("window.micPressLog.includes('release')")
        assert page.evaluate("window.micPressLog") == ["begin:hotkey", "release"]
        browser.close()
