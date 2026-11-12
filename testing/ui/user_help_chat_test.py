"""Exercise both production help UIs without model calls."""

import re
import sys
from pathlib import Path

from playwright.sync_api import Route, sync_playwright
import pytest

from testing.ui.canvas_source import read_ui_source


@pytest.mark.parametrize("owner", [True, False])
def test_help_settings_button_is_owner_only_and_requires_a_click(owner: bool) -> None:
    script = Path("static/js/chat-controller.js").read_text(encoding="utf-8")
    script = script.replace("export function", "function").replace("import { initializeChatEmotes } from './chat-emotes.js';", "")
    emotes = Path("static/js/chat-emotes.js").read_text(encoding="utf-8").replace("export function", "function")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page()

        def shell(route: Route) -> None:
            route.fulfill(body='<div id="chat-messages"></div>', content_type="text/html")

        page.route("http://narratron.test/", shell)
        page.goto("http://narratron.test/")
        page.add_script_tag(content="""
            window.applies = []; window.restarts = 0;
            window.fetch = async (url, options) => {
                if (!url.endsWith('/apply-settings')) return {ok: true, json: async () => url.startsWith('/api/chat') && window.message ? [window.message] : []};
                window.applies.push({url, body: JSON.parse(options.body), confirmation: options.headers['X-Narratron-Settings-Confirm']});
                if (window.applies.length === 1) return {ok: false, json: async () => ({detail: 'Settings changed. Ask again.'})};
                return {ok: true, json: async () => ({applied: true, agent_running: true, message: 'Settings saved and agent restarted.'})};
            };
        """)
        page.add_script_tag(content=emotes + script)
        page.evaluate("""async owner => {
            window.controller = initializeChatController({
                messagesContainer: document.querySelector('#chat-messages'), theaterId: 'stage',
                isTheaterOwner: () => owner, onSettingsApplied: async () => { window.restarts++; },
            });
            await controller.fetchChat();
            window.message = {type: 'user_help', author: 'Narratron User Help', text: 'Review this change.',
                settings_proposal: {config_digest: 'a'.repeat(64), changes: [{setting: 'image_generation.enabled', enabled: false}]},
                settings_summary: ['Disable AI image generation'],
            };
            document.querySelector('#chat-messages').appendChild(controller.renderChatMessage(message));
        }""", owner)
        button = page.get_by_role('button', name='Apply settings and restart agent')
        assert button.count() == (1 if owner else 0)
        assert page.evaluate("window.applies") == []
        if owner:
            assert page.get_by_text('Disable AI image generation').count() == 1
            button.click()
            page.get_by_role('status').filter(has_text='Settings changed. Ask again.').wait_for()
            assert button.is_enabled()
            assert page.evaluate('window.restarts') == 0
            button.click()
            page.get_by_role('status').filter(has_text='Settings saved and agent restarted.').wait_for()
            assert page.evaluate('window.restarts') == 1
            assert page.evaluate('window.applies[1]') == {
                'url': '/api/user-help/stage/apply-settings',
                'confirmation': '1',
                'body': {'config_digest': 'a' * 64, 'changes': [{'setting': 'image_generation.enabled', 'enabled': False}]},
            }
            page.evaluate("document.querySelector('#chat-messages').replaceChildren(controller.renderChatMessage(message))")
            assert page.get_by_role('button', name='Settings applied').is_disabled()
        browser.close()


def test_canvas_help_is_private_and_skips_the_live_agent() -> None:
    template = read_ui_source(Path("templates/canvas.html"))
    form = re.search(r'<form id="chat-form"[^>]*>.*?</form>', template, re.DOTALL)
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
    end = template.index('    <section class="features-carousel', start)
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
        assert not page.locator('#visitor-help-panel').is_visible()
        page.locator('#visitor-help-launcher').click()
        assert page.locator('#visitor-help-panel').is_visible()
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
        page.locator('#visitor-help-close').click()
        assert not page.locator('#visitor-help-panel').is_visible()
        page.locator('#visitor-help-launcher').click()
        assert page.locator('#visitor-help-panel').is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        browser.close()
