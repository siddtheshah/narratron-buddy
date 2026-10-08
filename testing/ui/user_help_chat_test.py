"""Exercise both production help UIs without model calls."""

import re
import sys
from pathlib import Path

from playwright.sync_api import Route, sync_playwright


def test_canvas_help_is_private_and_skips_the_live_agent() -> None:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    form = re.search(r'<form id="chat-form">.*?</form>', template, re.DOTALL)
    assert form is not None
    script = Path("static/js/chat-controller.js").read_text(encoding="utf-8")
    script = script.replace("export function", "function").replace("import { initializeChatEmotes } from './chat-emotes.js';", "")
    emotes = Path("static/js/chat-emotes.js").read_text(encoding="utf-8").replace("export function", "function")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 390, "height": 780})

        def shell(route: Route) -> None:
            route.fulfill(body=f'<div id="chat-messages"></div>{form.group()}', content_type="text/html")

        page.route("http://narratron.test/", shell)
        page.goto("http://narratron.test/")
        page.add_script_tag(content="""
            window.requests = []; window.forwarded = []; window.broadcasted = [];
            window.fetch = async (url, options) => {
                if (options?.method === 'POST') {
                    const body = JSON.parse(options.body);
                    window.requests.push({url, body});
                    const text = body.personalized ? 'Researched answer' : 'Private answer';
                    return {ok: true, json: async () => ({type: 'user_help', text, html: `<p>${text}</p>`,
                        help_mode: body.personalized ? 'personalized' : 'basic', can_personalize: true,
                        personalized_credit_cost: 0.15})};
                }
                return {ok: true, json: async () => []};
            };
        """)
        page.add_script_tag(content=emotes + script + """
            window.controller = initializeChatController({
                messagesContainer: document.querySelector('#chat-messages'),
                chatForm: document.querySelector('#chat-form'),
                chatInput: document.querySelector('#chat-input'),
                theaterId: 'stage', isCurrentOrator: () => true,
                getAgentWs: () => ({readyState: WebSocket.OPEN, send: value => window.forwarded.push(value)}),
                getAuthState: async () => ({authenticated: true, user: {username: 'alice'}}),
                popoutBroadcast: {postMessage: value => window.broadcasted.push(value)},
            });
        """)
        page.locator('#chat-input').fill('How do I start?')
        assert page.locator('[data-chat-command="help"]').inner_text() == '?'
        page.locator('[data-chat-command="help"]').click()
        page.wait_for_function("document.querySelector('#chat-messages').textContent.includes('Private answer')")
        assert page.evaluate("window.requests") == [{"url": "/api/user-help?theater_id=stage", "body": {"question": "How do I start?"}}]
        assert page.evaluate("window.forwarded") == []
        assert page.evaluate("window.broadcasted") == []
        page.evaluate("controller.fetchChat()")
        assert page.locator('#chat-messages').inner_text().count('Private answer') == 1
        assert 'only you' in page.locator('#chat-messages').inner_text()
        assert page.locator('[data-chat-command="help"]').inner_text() == '?'
        assert page.locator('[data-chat-command="help"]').get_attribute('title') == 'Find basic help and documentation (free)'
        upgrade = page.get_by_role('button', name='Personalized help · 0.15 cr')
        assert upgrade.count() == 1
        assert len(page.evaluate('window.requests')) == 1
        upgrade.click()
        page.wait_for_function("document.querySelector('#chat-messages').textContent.includes('Researched answer')")
        assert page.evaluate('window.requests[1].body') == {
            'question': 'How do I start?', 'personalized': True, 'quoted_credit_cost': 0.15,
        }
        assert page.evaluate('window.forwarded') == []
        assert page.evaluate('window.broadcasted') == []
        browser.close()


def test_visitor_help_is_free_and_displays_errors() -> None:
    template = Path("templates/join_splash.html").read_text(encoding="utf-8")
    styles = re.search(r"<style>(.*?)</style>", template, re.DOTALL)
    assert styles is not None
    start = template.index('    <section class="visitor-help"')
    end = template.index('    <aside class="host-theater-card"', start)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 390, "height": 780})

        def shell(route: Route) -> None:
            route.fulfill(body=template[start:end], content_type="text/html")

        page.route("http://narratron.test/", shell)
        page.goto("http://narratron.test/")
        page.add_style_tag(content=styles.group(1))
        page.add_script_tag(content="""
            window.requests = [];
            window.fetch = async (url, options) => {
                window.requests.push({url, body: JSON.parse(options.body)});
                const body = JSON.parse(options.body);
                const ok = window.requests.length <= 2;
                return {ok, json: async () => ok ? {
                    html: body.personalized ? '<p>Researched answer</p>' : '<p>Explore <a href="/docs">Docs</a>.</p>',
                    help_mode: body.personalized ? 'personalized' : 'basic', can_personalize: true,
                    personalized_credit_cost: 0.15,
                } : {detail: 'Please wait a moment.'}};
            };
        """)
        page.add_script_tag(content=Path("static/js/user-help.js").read_text(encoding="utf-8"))
        page.locator('#visitor-help-question').fill('What is Narratron?')
        page.locator('#visitor-help-form button').click()
        page.wait_for_function("document.querySelector('#visitor-help-log').textContent.includes('Explore')")
        assert page.evaluate("window.requests[0]") == {"url": "/api/user-help", "body": {"question": "What is Narratron?"}}
        assert page.locator('#visitor-help-log a').get_attribute('href') == '/docs'
        upgrade = page.get_by_role('button', name='Personalized help · 0.15 cr')
        assert upgrade.count() == 1
        assert len(page.evaluate('window.requests')) == 1
        upgrade.click()
        page.wait_for_function("document.querySelector('#visitor-help-log').textContent.includes('Researched answer')")
        assert page.evaluate('window.requests[1].body') == {
            'question': 'What is Narratron?', 'personalized': True, 'quoted_credit_cost': 0.15,
        }
        page.locator('#visitor-help-question').fill('What else?')
        page.locator('#visitor-help-form button').click()
        page.wait_for_function("document.querySelector('#visitor-help-status').textContent === 'Please wait a moment.'")
        assert page.locator('#visitor-help-question').input_value() == 'What else?'
        assert page.locator('#visitor-help-form button').is_enabled()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        browser.close()
