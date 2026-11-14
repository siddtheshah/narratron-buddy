"""Live Python/browser parity helper for local game packages."""
from pathlib import Path

from playwright.sync_api import Route, sync_playwright

from game_engine.game_runtime import GameStep
from game_engine.game_state import GameAction, GameState
from utils.frontend_build import ensure_canvas_assets_built


def assert_browser_matches_server(game: GameState, steps: list[GameStep]) -> None:
    ensure_canvas_assets_built()
    payload = game.payload()
    assert payload is not None and payload.get('client') is not None

    def route_request(route: Route) -> None:
        path = route.request.url.split('http://game.test', 1)[-1]
        if path == '/':
            route.fulfill(content_type='text/html', body='<!doctype html><title>Game preview</title>')
        else:
            route.fulfill(path=Path(path.split('?', 1)[0].lstrip('/')))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route('http://game.test/**', route_request)
        page.goto('http://game.test/')
        page.evaluate('''async payload => {
            const {createLocalGame} = await import('/static/generated/canvas/game-runtime.js');
            window.runtime = createLocalGame(payload.client);
            window.previewState = payload.state;
        }''', payload)
        for step in steps:
            preview = page.evaluate('''action => {
                const result = window.runtime.apply(window.previewState, action);
                window.previewState = result.state;
                return result;
            }''', step.model_dump(mode='json'))
            authoritative = game.act(GameAction(name=step.name, arguments=step.arguments, revision=game.revision))
            assert preview['state'] == game.state
            assert preview['view'] == authoritative['view']
        browser.close()


