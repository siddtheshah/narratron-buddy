"""Exercise the production chat composers in a browser without external services."""

import re
import sys
from pathlib import Path

import pytest
from playwright.sync_api import Route, expect, sync_playwright


@pytest.mark.parametrize("view", ["canvas", "popout"])
def test_chat_composer_sends_suggestions_and_wraps_text(view: str) -> None:
    template = Path(f"templates/{view}.html").read_text(encoding="utf-8")
    form = re.search(r'<form id="chat-form">.*?</form>', template, re.DOTALL)
    styles = re.search(r"<style>(.*?)</style>", template, re.DOTALL)
    assert form is not None
    assert styles is not None
    emotes_script = Path("static/js/chat-emotes.js").read_text(encoding="utf-8")
    emotes_script = emotes_script.replace("export function", "function")
    if view == "canvas":
        script = Path("static/js/chat-controller.js").read_text(encoding="utf-8")
        script = script.replace("export function", "function")
        script = script.replace("import { initializeChatEmotes } from './chat-emotes.js';", "")
        script += """
            window.controller = initializeChatController({
                messagesContainer: document.querySelector('#chat-messages'),
                chatForm: document.querySelector('#chat-form'),
                chatInput: document.querySelector('#chat-input'),
                getAuthState: async () => ({authenticated: true, user: {username: 'alice'}}),
            });
        """
    else:
        start = template.index("        initializeChatEmotes(chatForm, chatInput);")
        end = template.index("        fetchChat();\n        fetchLatestState();", start)
        script = """
            const chatForm = document.querySelector('#chat-form');
            const chatInput = document.querySelector('#chat-input');
            const theaterId = '';
            const currentChatUsername = 'alice';
            const myUserId = 'fallback';
            const fetchChat = () => {};
            const broadcast = {postMessage: () => {}};
        """ + template[start:end]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 360, "height": 700})

        def serve_shell(route: Route) -> None:
            route.fulfill(body=f'<div id="chat-messages" style="height: 350px"></div>{form.group()}')

        page.route("http://narratron.test/", serve_shell)
        page.goto("http://narratron.test/")
        page.add_style_tag(content=styles.group(1))
        page.add_style_tag(content=Path("static/css/chat.css").read_text(encoding="utf-8"))
        page.add_script_tag(content="""
            window.sent = [];
            window.fetch = async (url, options) => {
                if (options?.method === 'POST') {
                    window.sent.push(JSON.parse(options.body));
                    return {ok: true, json: async () => ({type: 'chat'})};
                }
                return {ok: true, json: async () => []};
            };
        """)
        page.add_script_tag(content=emotes_script + script)
        chat_input = page.get_by_role("textbox", name="Chat message")
        suggest = page.get_by_role("button", name="Send suggestion")
        send = page.get_by_role("button", name="Send", exact=True)

        assert page.evaluate("""() => {
            const input = document.querySelector('#chat-input').getBoundingClientRect();
            const actions = document.querySelector('.chat-composer-actions').getBoundingClientRect();
            const buttons = [...document.querySelectorAll('.chat-composer-actions > button, [data-chat-emotes-toggle]')]
                .map(button => button.getBoundingClientRect());
            return actions.top >= input.bottom && Math.abs(actions.width - input.width) < 1
                && buttons.every(button => button.left >= input.left && button.right <= input.right + 1)
                && Math.abs(buttons[0].top - buttons[1].top) < 1;
        }""")

        suggest.click()
        assert page.evaluate("window.sent.length") == 0
        chat_input.fill("Explore the moon")
        suggest.click()
        page.wait_for_function("window.sent.length === 1")
        assert page.evaluate("window.sent[0].text") == "/suggest Explore the moon"
        expect(chat_input).to_have_value("")

        chat_input.fill("/suggest Open the door")
        suggest.click()
        page.wait_for_function("window.sent.length === 2")
        assert page.evaluate("window.sent[1].text") == "/suggest Open the door"

        chat_input.fill("Hello everyone")
        send.click()
        page.wait_for_function("window.sent.length === 3")
        assert page.evaluate("window.sent[2].text") == "Hello everyone"

        chat_input.fill("First line")
        chat_input.press("Shift+Enter")
        chat_input.press("End")
        chat_input.press_sequentially("Second line")
        expect(chat_input).to_have_value("First line\nSecond line")
        assert page.evaluate("window.sent.length") == 3
        chat_input.press("Enter")
        page.wait_for_function("window.sent.length === 4")
        assert page.evaluate("window.sent[3].text") == "First line\nSecond line"

        expect(suggest).to_have_attribute("title", "Send as a suggestion (Ctrl+Enter)")
        expect(suggest).to_have_attribute("aria-keyshortcuts", "Control+Enter")
        chat_input.fill("Follow the trail")
        chat_input.press("Control+Enter")
        page.wait_for_function("window.sent.length === 5")
        assert page.evaluate("window.sent[4].text") == "/suggest Follow the trail"
        expect(chat_input).to_have_value("")

        chat_input.fill("/suggest Find shelter")
        chat_input.press("Control+Enter")
        page.wait_for_function("window.sent.length === 6")
        assert page.evaluate("window.sent[5].text") == "/suggest Find shelter"
        chat_input.press("Control+Enter")
        assert page.evaluate("window.sent.length") == 6

        emotes = page.get_by_role("button", name="Emotes", exact=True)
        palette = page.get_by_role("group", name="Emotes palette")
        expect(palette).to_be_hidden()
        chat_input.fill("Hello world")
        chat_input.evaluate("el => el.setSelectionRange(6, 11)")
        emotes.click()
        expect(palette).to_be_visible()
        expect(emotes).to_have_attribute("aria-expanded", "true")
        assert palette.evaluate("""el => {
            const bounds = el.getBoundingClientRect();
            return bounds.left >= 0 && bounds.right <= window.innerWidth;
        }""")
        palette.get_by_role("button", name="Dragon", exact=True).click()
        expect(chat_input).to_have_value("Hello 🐉")
        expect(chat_input).to_be_focused()
        expect(palette).to_be_hidden()
        assert page.evaluate("window.sent.length") == 6
        chat_input.press("Enter")
        page.wait_for_function("window.sent.length === 7")
        assert page.evaluate("window.sent[6].text") == "Hello 🐉"

        emotes.click()
        page.keyboard.press("Escape")
        expect(palette).to_be_hidden()
        expect(emotes).to_be_focused()
        expect(emotes).to_have_attribute("aria-expanded", "false")
        emotes.click()
        page.mouse.click(2, 2)
        expect(palette).to_be_hidden()
        emotes.click()
        expect(palette.get_by_role("button", name="Smile", exact=True)).to_be_focused()
        page.keyboard.press("Enter")
        expect(chat_input).to_have_value("😀")
        expect(palette).to_be_hidden()
        assert page.evaluate("window.sent.length") == 7

        chat_input.fill("word " * 100)
        assert chat_input.evaluate("el => el.scrollHeight > el.clientHeight && el.scrollWidth === el.clientWidth")
        chat_input.fill("x" * 500)
        assert chat_input.evaluate("el => el.scrollWidth === el.clientWidth")
        assert chat_input.evaluate("""el => {
            const style = getComputedStyle(el);
            return el.rows >= 2 && el.clientHeight >= 2 * parseFloat(style.lineHeight);
        }""")

        if view == "canvas":
            page.evaluate("""() => {
                const messages = document.querySelector('#chat-messages');
                messages.appendChild(controller.renderChatMessage({type: 'suggestion', author: 'alice', text: 'Own idea'}));
                messages.appendChild(controller.renderChatMessage({type: 'suggestion', author: 'bob', text: 'Other idea'}));
            }""")
            own_vote = page.locator('[data-suggestion-author="alice"] .suggestion-upvote-btn')
            other_vote = page.locator('[data-suggestion-author="bob"] .suggestion-upvote-btn')
            expect(own_vote).to_be_disabled()
            expect(other_vote).to_be_enabled()
            expect(page.locator('[data-vote-count-for="alice"]')).to_have_text("1 vote")
            page.evaluate("controller.setChatUsername('bob')")
            expect(own_vote).to_be_enabled()
            expect(other_vote).to_be_disabled()
        browser.close()
