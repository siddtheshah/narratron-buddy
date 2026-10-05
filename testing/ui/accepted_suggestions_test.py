"""Check accepted suggestion history using the production chat renderer."""

import sys
from pathlib import Path

from playwright.sync_api import Route, expect, sync_playwright


def test_accepted_suggestion_remains_visible_alongside_new_suggestion() -> None:
    script = Path("static/js/chat-controller.js").read_text(encoding="utf-8")
    script = script.replace("export function", "function")
    script = script.replace("import { initializeChatEmotes } from './chat-emotes.js';", "")
    emotes = Path("static/js/chat-emotes.js").read_text(encoding="utf-8")
    emotes = emotes.replace("export function", "function")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page()

        def serve_shell(route: Route) -> None:
            route.fulfill(body='<div id="chat-messages"></div>')

        page.route("http://narratron.test/", serve_shell)
        page.goto("http://narratron.test/")
        page.add_script_tag(content="""
            window.messages = [{author: 'alice', text: 'First idea', type: 'suggestion'}];
            window.suggestions = [{author: 'alice', upvote_count: 1, upvoters: ['alice']}];
            window.fetch = async url => ({ok: true, json: async () =>
                url.startsWith('/api/suggestions') ? window.suggestions : window.messages});
        """)
        page.add_script_tag(content=emotes + script + """
            window.controller = initializeChatController({
                messagesContainer: document.querySelector('#chat-messages'),
            });
        """)
        expect(page.locator('.suggestion-upvote-btn')).to_have_count(1)
        page.evaluate("""async () => {
            window.messages[0].status = 'accepted';
            window.suggestions = [];
            await window.controller.fetchChat();
            await window.controller.fetchSuggestions();
        }""")
        expect(page.locator('.suggestion-message')).to_have_count(1)
        expect(page.locator('.suggestion-accepted-badge')).to_have_text('✓ Accepted')
        expect(page.locator('.suggestion-upvote-btn')).to_have_count(0)
        expect(page.locator('.suggestion-withdraw-btn')).to_have_count(0)
        page.evaluate("""async () => {
            window.messages.push({author: 'alice', text: 'Second idea', type: 'suggestion'});
            window.suggestions = [{author: 'alice', upvote_count: 2, upvoters: ['alice', 'bob']}];
            await window.controller.fetchChat();
            window.controller.setChatUsername('alice');
        }""")
        expect(page.locator('.suggestion-message')).to_have_count(2)
        expect(page.locator('.suggestion-accepted-badge')).to_have_count(1)
        expect(page.locator('.suggestion-upvote-btn')).to_be_disabled()
        expect(page.locator('.suggestion-vote-count')).to_have_text('2 votes')
        browser.close()
