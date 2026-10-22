"""Compare browser previews with the authoritative CEL runtime."""
from pathlib import Path
from unittest.mock import Mock

from playwright.sync_api import Route, expect, sync_playwright
import pytest

from components.canvas.game_runtime import GameStep
from components.canvas.game_state import GameAction, GameState
from testing.game_fixtures import counter_definition, make_game


def assert_browser_matches_server(game: GameState, steps: list[GameStep]) -> None:
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
            const {createLocalGame} = await import('/static/js/game-runtime.js');
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


@pytest.mark.parametrize('package', ['counter', 'checkers'])
def test_browser_preview_matches_cel_state_and_view(tmp_path: Path, package: str) -> None:
    if package == 'counter':
        definition = counter_definition()
        definition.local_preview = True
        definition.view.props.update({
            'negativeDivision': '-7 / 2',
            'remainder': '-7 % 2',
            'boolean': '!false && (state.count >= 0 || false)',
            'membership': '"key" in {"key": 1}',
            'deepEquality': '[{"a": 1, "b": 2}] == [{"b": 2, "a": 1}]',
            'filteredMap': '[1, 2, 3].filter(x, x > 1).map(x, x * 2)',
            'filtered': '[1, 2, 3].filter(x, x != 2)',
            'all': '[1, 2].all(x, x < 3)',
            'exists': '[1, 2].exists(x, x == 2)',
            'unicodeSize': 'size("🌍")',
            'converted': 'int("3") + int(true)',
            'nullValue': 'null',
        })
        game = make_game(tmp_path, definition)
        steps = [GameStep(name='add', arguments={'amount': 2}), GameStep(name='add', arguments={'amount': 3}), GameStep(name='clear')]
    else:
        game = GameState(Path('adventures/example_adventure'), Mock(), Mock())
        steps = [GameStep(name='select', arguments={'index': 40}), GameStep(name='select', arguments={'index': 42}), GameStep(name='move', arguments={'source': 42, 'target': 35})]
    assert_browser_matches_server(game, steps)


@pytest.mark.parametrize('failure', ['guard', 'invariant', 'evaluation'])
def test_failed_preview_retains_position_and_shows_feedback(tmp_path: Path, failure: str) -> None:
    definition = counter_definition()
    definition.local_preview = True
    definition.transitions['add'].preview = True
    if failure == 'guard':
        definition.transitions['add'].guard = 'false'
    elif failure == 'invariant':
        definition.invariants = ['state.count == 0']
    else:
        definition.transitions['add'].result = '{"count": 1 / 0}'
    game = make_game(tmp_path, definition)
    assert game.error == ''
    html = '''<!doctype html><button id="toggle"></button><div id="game"></div>
    <script type="module">
    import {createGameRenderer} from '/static/js/game-renderer.js';
    window.renderer = createGameRenderer({container: document.querySelector('#game'),
        toggleButton: document.querySelector('#toggle'), canEdit: () => true,
        actionUrl: () => '/action', refresh: () => {}, initiallyOpen: true});
    </script>'''

    def route_request(route: Route) -> None:
        path = route.request.url.split('http://game.test', 1)[-1]
        if path == '/':
            route.fulfill(content_type='text/html', body=html)
        else:
            route.fulfill(path=Path(path.split('?', 1)[0].lstrip('/')))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.route('http://game.test/**', route_request)
        page.goto('http://game.test/')
        page.wait_for_function('window.renderer !== undefined')
        page.evaluate('payload => window.renderer.render(payload)', game.payload())
        page.get_by_role('button', name='Add one').click()
        expect(page.get_by_role('alert')).to_have_text(
            'This action is not permitted in the current game state.' if failure == 'guard' else
            'Game action could not be completed; the action was undone. Try another action.',
        )
        expect(page.locator('[data-game-id="counter"]')).to_have_text('Count: 0')
        expect(page.get_by_role('button', name='Submit', exact=True)).to_have_count(0)
        assert game.revision == 0 and game.state == {'count': 0}
        browser.close()
