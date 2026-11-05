"""Generic structured forms submit typed arguments without executing entered text."""
from pathlib import Path

from playwright.sync_api import Route, expect, sync_playwright

from game_engine.game_runtime import GameAction, GameDefinition
from testing.game_fixtures import make_game


def test_form_text_numbers_bounds_and_permissions(tmp_path: Path) -> None:
    definition = GameDefinition.model_validate({
        "version": 2,
        "initial": {"text": "", "number": 0},
        "state_schema": {
            "type": "object", "required": ["text", "number"], "additionalProperties": False,
            "properties": {"text": {"type": "string", "maxLength": 1000}, "number": {"type": "integer"}},
        },
        "transitions": {"save": {
            "parameters": {
                "type": "object", "required": ["text", "number"], "additionalProperties": False,
                "properties": {"text": {"type": "string", "maxLength": 1000}, "number": {"type": "integer"}},
            },
            "result": '{"text": action.text, "number": action.number}',
        }},
        "view": {"kind": "column", "children": [
            {"kind": "text", "props": {"text": "state.text"}},
            {"kind": "form", "props": {"label": '"Example form"'},
             "action": {"name": '"save"', "arguments": '{"number": 3}'}, "children": [
                 {"kind": "input", "props": {
                     "name": '"text"', "label": '"Text"', "labelHidden": "true", "required": "true", "maxLength": "9000",
                 }},
                 {"kind": "input", "props": {
                     "name": '"number"', "label": '"Number"', "type": '"number"',
                     "value": "0", "required": "true", "step": "1",
                 }},
                 {"kind": "button", "props": {"text": '"Save"'}},
                 {"kind": "button", "props": {"text": '"Clear text"', "clearInputs": '{"text": ""}'}},
             ]},
        ]},
    })
    game = make_game(tmp_path, definition)
    submissions: list[GameAction] = []
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
            submissions.append(action)
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
        text = page.get_by_label("Text", exact=True)
        expect(text).to_have_attribute("maxlength", "1000")
        assert page.locator("label.game-input-field", has=text).count() == 0
        page.get_by_role("button", name="Save", exact=True).click()
        assert submissions == []
        entered = '<img src=x onerror="window.compromised=true">'
        text.fill(entered)
        page.get_by_label("Number", exact=True).fill("-2")
        text.press("Enter")
        expect(page.get_by_text(entered, exact=True)).to_be_visible()
        assert submissions[0].arguments == {"text": entered, "number": -2}
        assert page.locator("#root img").count() == 0
        assert page.evaluate("window.compromised === undefined")
        text.fill("Draft text")
        page.get_by_label("Number", exact=True).fill("5")
        page.get_by_role("button", name="Clear text", exact=True).click()
        expect(text).to_have_value("")
        expect(page.get_by_label("Number", exact=True)).to_have_value("5")
        assert len(submissions) == 1
        text.fill("x" * 1001)
        expect(text).to_have_value("x" * 1000)
        text.evaluate("input => { input.value = 'x'.repeat(1001); }")
        page.get_by_role("button", name="Save", exact=True).click()
        assert len(submissions) == 1
        page.evaluate("window.editable = false; renderer.render()")
        expect(text).to_be_disabled()
        expect(page.get_by_role("button", name="Save", exact=True)).to_be_disabled()
        expect(page.get_by_role("button", name="Clear text", exact=True)).to_be_disabled()
        browser.close()
