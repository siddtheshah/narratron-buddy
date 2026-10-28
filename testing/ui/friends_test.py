"""Browser coverage for profile friend controls and one-click theater joining."""

from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from playwright.sync_api import Route, expect, sync_playwright
from pydantic import JsonValue


def test_profile_requests_and_friend_join_menu() -> None:
    relationships: list[dict[str, str]] = []
    joins: list[str] = []
    long_name = 'Forest story with a very long theater name that should be truncated'
    theaters: list[dict[str, JsonValue]] = [
        {'username': 'Dana', 'theater_id': 'dana-stage', 'name': 'Sun story', 'online': True},
        {'username': 'Bobby', 'theater_id': 'bob-stage', 'name': 'Dragon story', 'online': True,
         'active_viewers': ['Bobby', '<Alice>']},
        {'username': 'Bobby', 'theater_id': 'bob-other', 'name': long_name, 'online': True},
        {'username': 'Bobby', 'theater_id': 'bob-offline', 'name': 'Sleeping story', 'online': False},
        {'username': 'Carol', 'theater_id': 'carol-stage', 'name': 'Moon story', 'online': False},
    ]

    def serve(route: Route) -> None:
        path = urlsplit(route.request.url).path
        if path == '/static/js/friends.js':
            route.fulfill(body=Path('static/js/friends.js').read_text(encoding='utf-8'), content_type='text/javascript')
        elif path == '/api/friends':
            route.fulfill(json=relationships)
        elif path == '/api/friends/Bobby':
            if route.request.post_data_json['action'] == 'request':
                relationships[:] = [{'username': 'Bobby', 'status': 'outgoing'}]
            else:
                relationships.clear()
            route.fulfill(json={'status': 'ok'})
        elif path.startswith('/api/users/'):
            owner = '/Alice/' in path
            route.fulfill(json={'username': 'Alice' if owner else 'Bobby', 'is_owner': owner,
                                'bio': '', 'stats': None, 'stamps': []})
        elif path == '/api/friend-theaters':
            route.fulfill(json=theaters)
        elif path == '/api/friend-theaters/bob-stage/join':
            joins.append(path)
            route.fulfill(json={'theater_id': 'bob-stage'})
        elif path == '/canvas':
            route.fulfill(body='<h1>Theater canvas</h1>', content_type='text/html')
        elif path in ('/users/Bobby', '/users/Alice', '/join'):
            template = 'join_splash.html' if path == '/join' else 'profile.html'
            html = Path('templates', template).read_text(encoding='utf-8')
            # Keep the actual markup and feature code; stub unrelated libraries and integrations.
            html = re.sub(r'<script[^>]+src="(?!/static/js/friends\.js)[^"]+"[^>]*></script>', '', html)
            html = re.sub(r'<link[^>]+(?:href="https?://)[^>]*>', '', html)
            html = html.replace('<head>', '''<head><script>
              function getAuthState() { return Promise.resolve({authenticated:true,user:{username:'Alice'}}); }
              function Splide() { this.mount = () => {}; }
            </script>''')
            route.fulfill(body=html, content_type='text/html')
        else:
            route.fulfill(body='', content_type='text/plain')

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge' if sys.platform == 'win32' else None)
        page = browser.new_page(viewport={'width': 1100, 'height': 900})
        errors: list[str] = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/*', serve)
        page.goto('http://narratron.test/users/Bobby')
        page.get_by_role('button', name='Send Friend Request', exact=True).click()
        expect(page.get_by_text('Friend request sent', exact=True)).to_be_visible()
        page.get_by_role('button', name='Cancel request', exact=True).click()
        expect(page.get_by_role('button', name='Send Friend Request', exact=True)).to_be_visible()
        relationships[:] = [{'username': 'Bobby', 'status': 'incoming'}]
        page.goto('http://narratron.test/users/Alice')
        page.get_by_role('tab', name='Friends & requests').click()
        expect(page.get_by_role('heading', name='Incoming requests')).to_be_visible()
        expect(page.get_by_role('button', name='Accept', exact=True)).to_be_visible()
        page.get_by_role('button', name='Decline', exact=True).click()
        expect(page.get_by_role('button', name='Accept', exact=True)).to_have_count(0)
        page.goto('http://narratron.test/join')
        groups = page.locator('#friend-theaters-list .friend-username')
        expect(groups).to_have_text(['Bobby', 'Dana'])
        expect(page.locator('#friend-active-theaters')).to_contain_text('Select a friend')
        dragon = page.get_by_role('radio', name='Dragon story', exact=True)
        expect(dragon).not_to_be_visible()
        groups.first.click()
        expect(dragon).to_be_visible()
        expect(page.locator('.friend-theater-viewers').first).to_have_text('2 viewers')
        expect(page.locator('.friend-theater-viewers').first).to_have_attribute('title', 'Viewing: @Bobby, @<Alice>')
        expect(page.locator('.friend-theater-viewers').last).to_have_text('0 viewers')
        expect(page.get_by_role('radio', name=long_name, exact=True)).to_be_visible()
        expect(page.locator('#friend-theaters-list')).not_to_contain_text('Sleeping story')
        expect(page.locator('#friend-theaters-list')).not_to_contain_text('Carol')
        expect(page.get_by_role('radio', name='Sun story', exact=True)).not_to_be_visible()
        expect(groups.first).to_have_attribute('aria-pressed', 'true')
        usernames_box = page.locator('.friend-usernames').bounding_box()
        theaters_box = page.locator('#friend-active-theaters').bounding_box()
        assert usernames_box is not None and theaters_box is not None
        assert usernames_box['x'] + usernames_box['width'] <= theaters_box['x'] + 1
        groups.get_by_text('Dana', exact=True).click()
        expect(page.get_by_role('radio', name='Sun story', exact=True)).to_be_visible()
        expect(dragon).not_to_be_visible()
        groups.first.focus()
        page.keyboard.press('Enter')
        expect(dragon).to_be_visible()
        # Polling preserves the selected friend.
        theaters[1]['active_viewers'] = ['Bobby']
        with page.expect_response('**/api/friend-theaters'):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(page.locator('.friend-theater-viewers').first).to_have_text('1 viewer')
        expect(dragon).to_be_visible()
        page.set_viewport_size({'width': 390, 'height': 844})
        expect(page.locator('#friend-theaters')).to_have_attribute('aria-hidden', 'true')
        page.get_by_role('button', name="Friends' theaters", exact=True).click()
        expect(page.locator('#join-adventure')).to_have_attribute('aria-hidden', 'true')
        page.wait_for_function("document.getElementById('friend-theaters').getBoundingClientRect().right <= window.innerWidth")
        page.locator('.join-options-track').evaluate("el => Promise.all(el.getAnimations().map(a => a.finished))")
        expect(page.get_by_role('button', name='Join / adventure', exact=True)).to_be_focused()
        usernames_box = page.locator('.friend-usernames').bounding_box()
        theaters_box = page.locator('#friend-active-theaters').bounding_box()
        assert usernames_box is not None and theaters_box is not None
        assert usernames_box['x'] + usernames_box['width'] <= theaters_box['x'] + 1
        assert theaters_box['x'] + theaters_box['width'] <= 390
        long_title = page.locator('.friend-theater-name').last
        expect(long_title).to_have_attribute('title', long_name)
        assert long_title.evaluate('(el) => el.scrollWidth > el.clientWidth && getComputedStyle(el).textOverflow === "ellipsis"')
        name_box = long_title.bounding_box()
        count_box = page.locator('.friend-theater-viewers').last.bounding_box()
        assert name_box is not None and count_box is not None
        assert name_box['x'] + name_box['width'] <= count_box['x']
        page.get_by_role('button', name='Join / adventure', exact=True).click()
        expect(page.locator('#friend-theaters')).to_have_attribute('aria-hidden', 'true')
        expect(page.get_by_role('button', name="Friends' theaters", exact=True)).to_be_focused()
        page.set_viewport_size({'width': 1100, 'height': 900})
        join = page.get_by_role('button', name='Join theater', exact=True)
        expect(join).to_be_disabled()
        expect(page.locator('#friend-active-theaters h3')).to_have_count(0)
        dragon.check()
        expect(join).to_be_enabled()
        assert joins == []
        original_theaters = theaters.copy()
        theaters.extend([
            {'username': f'Friend {index:02}', 'theater_id': f'stage-{index}', 'name': 'Story', 'online': True}
            for index in range(20)
        ])
        with page.expect_response('**/api/friend-theaters'):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(groups).to_have_count(22)
        expect(dragon).to_be_checked()
        expect(join).to_be_enabled()
        expect(page.locator('#friend-theaters-pagination')).to_have_count(0)
        assert page.locator('.friend-usernames').evaluate('(el) => el.scrollHeight > el.clientHeight')
        usernames_box = page.locator('.friend-usernames').bounding_box()
        browser_box = page.locator('.friend-theaters-browser').bounding_box()
        assert usernames_box is not None and browser_box is not None
        assert abs(usernames_box['height'] - (browser_box['height'] - 2)) < 1
        groups.last.click()
        expect(join).to_be_disabled()
        theaters[:] = original_theaters
        with page.expect_response('**/api/friend-theaters'):
            page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(groups).to_have_text(['Bobby', 'Dana'])
        expect(join).to_be_disabled()
        groups.first.click()
        dragon.check()
        join.click()
        expect(page).to_have_url('http://narratron.test/canvas?theater_id=bob-stage')
        assert joins == ['/api/friend-theaters/bob-stage/join']
        theaters[:] = [theater for theater in theaters if not theater['online']]
        page.goto('http://narratron.test/join')
        expect(page.locator('#friend-theaters-list')).to_contain_text('You do not have any friends yet.')
        gift_link = page.get_by_role('link', name='Create a gift link', exact=True)
        expect(gift_link).to_have_attribute('href', '/users/Alice#gifts')
        gift_link.click()
        expect(page).to_have_url('http://narratron.test/users/Alice#gifts')
        expect(page.get_by_role('heading', name='Gift credits', exact=True)).to_be_visible()
        expect(groups).to_have_count(0)
        assert errors == []
        browser.close()
