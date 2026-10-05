"""Verify help Markdown in both production chat renderers."""

import sys
from pathlib import Path

import pytest
from playwright.sync_api import Route, sync_playwright

from utils.markdown import render_markdown


@pytest.mark.parametrize("view", ["canvas", "popout"])
def test_help_markdown_renders_and_escapes_model_html(view: str) -> None:
    answer = (
        "## Start an adventure\n\n"
        "1. Open **Settings**.\n2. Press `Shift+A`.\n\n"
        "See [the guide](https://example.test/guide).\n\n"
        "```yaml\nadventure_mode: true\n```\n\n"
        '<img src=x onerror="window.injected=true">'
    )
    message = {"author": "Narratron User Help", "type": "user_help",
               "text": answer, "html": render_markdown(answer)}
    if view == "canvas":
        script = Path("static/js/chat-controller.js").read_text(encoding="utf-8")
        script = script.replace("export function", "function")
        script = script.replace("import { initializeChatEmotes } from './chat-emotes.js';", "")
        setup = "window.renderChatMessage = initializeChatController().renderChatMessage;"
    else:
        template = Path("templates/popout.html").read_text(encoding="utf-8")
        start = template.index("        function getAuthorColor(")
        end = template.index('        let currentChatUsername = "";', start)
        script = template[start:end]
        setup = ""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 400, "height": 700})
        def serve_chat_shell(route: Route) -> None:
            route.fulfill(body="<div id='messages'></div>")

        page.route("http://narratron.test/", serve_chat_shell)
        page.goto("http://narratron.test/")
        page.add_style_tag(content=Path("static/css/chat.css").read_text(encoding="utf-8"))
        page.add_script_tag(content="window.fetch = async () => ({ok: true, json: async () => []});")
        page.add_script_tag(content=script + setup)
        page.evaluate("msg => document.querySelector('#messages').appendChild(renderChatMessage(msg))", message)

        help_body = page.locator(".help-markdown")
        assert help_body.locator("h2").inner_text() == "Start an adventure"
        assert help_body.locator("ol li").count() == 2
        assert help_body.locator("strong").inner_text() == "Settings"
        assert help_body.locator("li code").inner_text() == "Shift+A"
        assert help_body.locator("pre code").inner_text() == "adventure_mode: true"
        assert help_body.locator("a").get_attribute("href") == "https://example.test/guide"
        assert help_body.locator("img").count() == 0
        assert '<img src=x onerror="window.injected=true">' in help_body.inner_text()
        assert page.evaluate("window.injected === undefined")

        # Regular messages stay literal even if a message includes an HTML field.
        page.evaluate(
            "msg => document.querySelector('#messages').appendChild(renderChatMessage(msg))",
            {"author": "Player", "text": "**hello**", "html": "<strong>hello</strong>"},
        )
        assert page.locator(".chat-message").last.locator(".chat-text").inner_text() == "**hello**"
        assert page.locator(".chat-message").last.locator("strong").count() == 0
        browser.close()
