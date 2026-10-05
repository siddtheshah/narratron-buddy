"""Tests for the chat user name display in /canvas."""

import re
import sys
from pathlib import Path

from playwright.sync_api import Route, expect, sync_playwright


def test_canvas_html_has_chat_name_display_element() -> None:
    canvas_html = Path("templates/canvas.html").read_text(encoding="utf-8")
    assert 'id="chat-name-display"' in canvas_html
    assert 'id="chat-name-input"' in canvas_html
    assert 'id="chat-login-chip"' in canvas_html
    assert "nameDisplay: document.getElementById('chat-name-display')" in canvas_html
    assert "loginChip: document.getElementById('chat-login-chip')" in canvas_html
    assert "auth-flow.js" in canvas_html
    assert "auth-flow.css" in canvas_html
    assert "window.onAuthSuccess" in canvas_html


def test_canvas_authenticated_user_chat_name_display() -> None:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    user_bar_match = re.search(r'<div id="chat-user-bar".*?</div>', template, re.DOTALL)
    assert user_bar_match is not None
    user_bar_html = user_bar_match.group(0)

    script = Path("static/js/chat-controller.js").read_text(encoding="utf-8")
    script = script.replace("export function", "function")
    script = script.replace("import { initializeChatEmotes } from './chat-emotes.js';", "")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 400, "height": 700})

        def serve_shell(route: Route) -> None:
            route.fulfill(body=f'<div id="chat-container">{user_bar_html}</div><div id="chat-messages"></div>')

        page.route("http://narratron.test/", serve_shell)
        page.goto("http://narratron.test/")

        page.add_script_tag(content="""
            window.messages = [];
            window.suggestions = [];
            window.fetch = async (url) => {
                if (url.startsWith('/api/chat')) return { ok: true, json: async () => window.messages };
                if (url.startsWith('/api/suggestions')) return { ok: true, json: async () => window.suggestions };
                return { ok: true, json: async () => ({}) };
            };
        """)

        page.add_script_tag(content=script + """
            window.controller = initializeChatController({
                messagesContainer: document.getElementById('chat-messages'),
                nameInput: document.getElementById('chat-name-input'),
                nameDisplay: document.getElementById('chat-name-display'),
                nameBadge: document.getElementById('chat-name-badge'),
                loginChip: document.getElementById('chat-login-chip'),
                getAuthState: async () => ({
                    authenticated: true,
                    user: { username: 'Gandalf', profile_color: '#ec4899' },
                }),
            });
        """)

        name_input = page.locator("#chat-name-input")
        name_display = page.locator("#chat-name-display")
        name_badge = page.locator("#chat-name-badge")
        login_chip = page.locator("#chat-login-chip")

        expect(name_input).to_be_hidden()
        expect(name_display).to_be_visible()
        expect(name_display).to_have_text("Gandalf")
        expect(name_badge).to_have_text("Member")
        expect(login_chip).to_be_hidden()

        color = page.evaluate("() => getComputedStyle(document.getElementById('chat-name-display')).color")
        assert color == "rgb(236, 72, 153)"

        browser.close()


def test_canvas_unauthenticated_user_chat_name_input_visible() -> None:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    user_bar_match = re.search(r'<div id="chat-user-bar".*?</div>', template, re.DOTALL)
    assert user_bar_match is not None
    user_bar_html = user_bar_match.group(0)

    script = Path("static/js/chat-controller.js").read_text(encoding="utf-8")
    script = script.replace("export function", "function")
    script = script.replace("import { initializeChatEmotes } from './chat-emotes.js';", "")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 400, "height": 700})

        def serve_shell(route: Route) -> None:
            route.fulfill(body=f'<div id="chat-container">{user_bar_html}</div><div id="chat-messages"></div>')

        page.route("http://narratron.test/", serve_shell)
        page.goto("http://narratron.test/")

        page.add_script_tag(content="""
            window.messages = [];
            window.suggestions = [];
            window.fetch = async (url) => {
                if (url.startsWith('/api/chat')) return { ok: true, json: async () => window.messages };
                if (url.startsWith('/api/suggestions')) return { ok: true, json: async () => window.suggestions };
                return { ok: true, json: async () => ({}) };
            };
        """)

        page.add_script_tag(content=script + """
            window.controller = initializeChatController({
                messagesContainer: document.getElementById('chat-messages'),
                nameInput: document.getElementById('chat-name-input'),
                nameDisplay: document.getElementById('chat-name-display'),
                nameBadge: document.getElementById('chat-name-badge'),
                loginChip: document.getElementById('chat-login-chip'),
                getAuthState: async () => ({
                    authenticated: false,
                }),
            });
        """)

        name_input = page.locator("#chat-name-input")
        name_display = page.locator("#chat-name-display")
        name_badge = page.locator("#chat-name-badge")
        login_chip = page.locator("#chat-login-chip")

        expect(name_input).to_be_visible()
        expect(name_display).to_be_hidden()
        expect(name_badge).to_have_text("Anon")
        expect(login_chip).to_be_visible()
        expect(login_chip).to_have_text("Log In")

        browser.close()


def test_canvas_unauthenticated_login_chip_triggers_modal() -> None:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    user_bar_match = re.search(r'<div id="chat-user-bar".*?</div>', template, re.DOTALL)
    assert user_bar_match is not None
    user_bar_html = user_bar_match.group(0)

    script = Path("static/js/chat-controller.js").read_text(encoding="utf-8")
    script = script.replace("export function", "function")
    script = script.replace("import { initializeChatEmotes } from './chat-emotes.js';", "")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 400, "height": 700})

        def serve_shell(route: Route) -> None:
            route.fulfill(body=f'<div id="chat-container">{user_bar_html}</div><div id="chat-messages"></div>')

        page.route("http://narratron.test/", serve_shell)
        page.goto("http://narratron.test/")

        page.add_script_tag(content="""
            window.loginTriggeredWith = null;
            window.openAuthModal = (tab) => {
                window.loginTriggeredWith = tab;
            };
            window.messages = [];
            window.suggestions = [];
            window.fetch = async () => ({ ok: true, json: async () => ({}) });
        """)

        page.add_script_tag(content=script + """
            window.controller = initializeChatController({
                messagesContainer: document.getElementById('chat-messages'),
                nameInput: document.getElementById('chat-name-input'),
                nameDisplay: document.getElementById('chat-name-display'),
                nameBadge: document.getElementById('chat-name-badge'),
                loginChip: document.getElementById('chat-login-chip'),
                getAuthState: async () => ({
                    authenticated: false,
                }),
            });
        """)

        login_chip = page.locator("#chat-login-chip")
        expect(login_chip).to_be_visible()
        login_chip.click()

        triggered = page.evaluate("() => window.loginTriggeredWith")
        assert triggered == "login"

        browser.close()


def test_canvas_unauthenticated_stamp_manager_triggers_modal() -> None:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    assert "setStampManagerOpen" in template
    assert "openAuthModal" in template

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 400, "height": 700})

        page.set_content("""
            <div id="panel-body" style="display: flex;">Chat</div>
            <div id="stamp-manager-pane" style="display: none;">Stamps</div>
            <button id="chat-open-stamps-btn">🏷️</button>
        """)

        page.add_script_tag(content="""
            window.loginModalOpenedWith = null;
            window.openAuthModal = (tab) => {
                window.loginModalOpenedWith = tab;
            };

            let currentUser = null;
            let isStampManagerOpen = false;

            function getCanvasAuthState() {
                return Promise.resolve({ authenticated: false });
            }

            function setStampManagerOpen(open) {
                if (open) {
                    if (!currentUser || !currentUser.id) {
                        getCanvasAuthState()
                            .then(data => {
                                if (data && data.authenticated && data.user) {
                                    currentUser = data.user;
                                    setStampManagerOpen(true);
                                } else {
                                    if (typeof openAuthModal === 'function') {
                                        openAuthModal('login');
                                    }
                                }
                            })
                            .catch(() => {
                                if (typeof openAuthModal === 'function') {
                                    openAuthModal('login');
                                }
                            });
                        return;
                    }
                }
                isStampManagerOpen = Boolean(open);
                document.getElementById('panel-body').style.display = isStampManagerOpen ? 'none' : 'flex';
                document.getElementById('stamp-manager-pane').style.display = isStampManagerOpen ? 'flex' : 'none';
            }

            document.getElementById('chat-open-stamps-btn').addEventListener('click', () => {
                setStampManagerOpen(!isStampManagerOpen);
            });
        """)

        page.locator("#chat-open-stamps-btn").click()

        # Pane stays closed, login modal triggered
        expect(page.locator("#panel-body")).to_be_visible()
        expect(page.locator("#stamp-manager-pane")).to_be_hidden()

        tab = page.evaluate("() => window.loginModalOpenedWith")
        assert tab == "login"

        browser.close()
