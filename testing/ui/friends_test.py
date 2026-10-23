"""Browser coverage for profile friend controls and one-click theater joining."""

from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from playwright.sync_api import Route, expect, sync_playwright


def test_profile_requests_and_friend_join_menu() -> None:
    relationships: list[dict[str, str]] = []
    joins: list[str] = []

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
            route.fulfill(json=[{'username': 'Bobby', 'theater_id': 'bob-stage', 'name': 'Dragon story', 'online': True},
                                {'username': 'Carol', 'theater_id': 'carol-stage', 'name': 'Moon story', 'online': False}])
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
        expect(page.get_by_role('button', name=re.compile('Bobby.*Narratron online'))).to_be_visible()
        expect(page.get_by_role('button', name=re.compile('Carol.*Narratron offline'))).to_be_visible()
        page.set_viewport_size({'width': 390, 'height': 844})
        expect(page.locator('#friend-theaters')).to_have_attribute('aria-hidden', 'true')
        page.get_by_role('button', name="Friends' theaters", exact=True).click()
        expect(page.locator('#join-adventure')).to_have_attribute('aria-hidden', 'true')
        page.wait_for_function("document.getElementById('friend-theaters').getBoundingClientRect().right <= window.innerWidth")
        expect(page.get_by_role('button', name='Join / adventure', exact=True)).to_be_focused()
        page.get_by_role('button', name='Join / adventure', exact=True).click()
        expect(page.locator('#friend-theaters')).to_have_attribute('aria-hidden', 'true')
        expect(page.get_by_role('button', name="Friends' theaters", exact=True)).to_be_focused()
        page.set_viewport_size({'width': 1100, 'height': 900})
        expect(page.get_by_role('button', name=re.compile('Bobby.*Narratron online'))).to_be_visible()
        page.get_by_role('button', name=re.compile('Bobby.*Narratron online')).click()
        expect(page).to_have_url('http://narratron.test/canvas?theater_id=bob-stage')
        assert joins == ['/api/friend-theaters/bob-stage/join']
        assert errors == []
        browser.close()
