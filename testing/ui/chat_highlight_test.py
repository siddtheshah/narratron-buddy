"""Check current-thought highlighting in the main canvas and popout."""

from pathlib import Path
import sys

import pytest
from playwright.sync_api import Route, expect, sync_playwright


@pytest.mark.parametrize("template_name", ["canvas", "popout"])
def test_chat_highlight_updates_without_changing_message(template_name: str) -> None:
    template = Path(f"templates/{template_name}.html").read_text(encoding="utf-8")
    css = template.split("<style>", 1)[1].split("</style>", 1)[0]
    if template_name == "canvas":
        start = template.index("        let lastAgentThought = null;")
        script = template[start:template.index("        // Doodle Logic", start)]
        script += "\nfunction update(data) { updateAgentThought(data.agent_thought); }"
    else:
        start = template.index("                const thought = data.agent_thought")
        script = "function update(data) {" + template[start:template.index("                if (data.prompt)", start)] + "}"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page()

        def serve(route: Route) -> None:
            route.fulfill(body=f"""<style>{css}</style>
                <div class="agent-thought-card"><div id="agent-thought-text"
                class="agent-thought-text"></div></div>""")

        page.route("http://narratron.test/", serve)
        page.goto("http://narratron.test/")
        page.add_script_tag(content="""
            const agentThoughtText = document.getElementById('agent-thought-text');
            const disconnectedIndicator = null;
        """ + script)
        card = page.locator(".agent-thought-card")
        page.evaluate("update({agent_thought: {text: 'Hello', highlighted: false}})")
        expect(card).not_to_have_class("agent-thought-card is-highlighted")
        normal_shadow = card.evaluate("el => getComputedStyle(el).boxShadow")
        page.evaluate("update({agent_thought: {text: 'Hello', highlighted: true}})")
        expect(card).to_have_class("agent-thought-card is-highlighted")
        assert card.evaluate("el => getComputedStyle(el).boxShadow") != normal_shadow
        page.evaluate("update({agent_thought: {text: 'Hello', highlighted: false}})")
        expect(card).to_have_class("agent-thought-card")
        expect(page.locator("#agent-thought-text")).to_have_text("Hello")
        browser.close()
