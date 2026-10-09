"""Browser checks for viewer collaboration status and the compact panel header."""

import sys
from pathlib import Path

import pytest
from playwright.sync_api import Route, expect, sync_playwright


@pytest.mark.parametrize("viewport_width", [1000, 1400])
def test_viewer_collaboration_bar_and_help(viewport_width: int) -> None:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    css = Path("static/css/chat.css").read_text(encoding="utf-8")
    inline_css = template.split("<style>", 1)[1].split("</style>", 1)[0]
    start = template.index('    <div id="chat-sidebar">')
    sidebar = template[start:template.index('    <script type="module">', start)]
    start = template.index('    <div id="viewer-collab-guide-modal"')
    modal = template[start:template.index("    <!-- Adventure Mode Guide Modal -->", start)]
    start = template.index("        function updateViewerCollabBar(enabled)")
    bar_script = template[start:template.index("        function setSuggestionsHidden", start)]
    start = template.index("        const viewerCollabGuideModal =")
    guide_script = template[start:template.index("        document.addEventListener('keydown'", start)]
    start = template.index("        if (viewerCollabGuideBtn) {")
    help_binding = template[start:template.index("        setSuggestionsHidden(localStorage", start)]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": viewport_width, "height": 800})

        def serve_shell(route: Route) -> None:
            route.fulfill(body=f"<style>{css}{inline_css}</style><div style='height:650px'>{sidebar}</div>{modal}")

        page.route("http://narratron.test/", serve_shell)
        page.goto("http://narratron.test/")
        page.add_script_tag(content="""
            let activeOrator = false;
            let viewerCollabEnabled = false;
            const theaterId = 'test';
            const isCurrentOrator = () => activeOrator;
            const viewerCollabBar = document.getElementById('viewer-collab-bar');
            const viewerCollabBarStatus = document.getElementById('viewer-collab-bar-status');
            const viewerCollabGuideBtn = document.getElementById('viewer-collab-guide-btn');
        """ + bar_script + guide_script + help_binding)
        page.evaluate("updateViewerCollabStatus(false)")
        bar = page.locator("#viewer-collab-bar")
        expect(bar).to_be_visible()
        expect(page.locator("#viewer-collab-bar-status")).to_have_text("Collaboration: Off")
        popout = page.get_by_role("button", name="Pop out chat into separate window")
        expect(popout).to_have_text("")
        expect(popout.locator("svg")).to_be_visible()
        page.locator("#chat-name-input").fill("Moonlit Wanderer 42")
        page.locator("#chat-login-chip").evaluate("element => element.style.display = 'inline-flex'")
        assert page.locator("#chat-name-input").evaluate("element => element.clientWidth") > 80
        bar_box = bar.bounding_box()
        tabs_box = page.locator("#chat-user-bar").bounding_box()
        thought_box = page.locator("#agent-thought-card").bounding_box()
        assert bar_box is not None and tabs_box is not None and thought_box is not None
        assert bar_box["y"] >= tabs_box["y"] + tabs_box["height"]
        assert thought_box["y"] >= bar_box["y"] + bar_box["height"]
        page.get_by_role("button", name="What is collaboration?").click()
        expect(page.get_by_role("dialog", name="Collaborate with Narratron")).to_be_visible()
        expect(page.locator("#viewer-collab-status")).to_contain_text("currently off")
        page.evaluate("viewerCollabEnabled = true; updateViewerCollabStatus(true)")
        expect(page.locator("#viewer-collab-bar-status")).to_have_text("Collaboration: On")
        expect(page.locator("#viewer-collab-status")).to_contain_text("top viewer suggestion")
        page.get_by_role("button", name="Close collaboration guide").click()
        expect(page.get_by_role("dialog", name="Collaborate with Narratron")).to_be_hidden()
        page.evaluate("activeOrator = true; updateViewerCollabBar(viewerCollabEnabled)")
        expect(bar).to_be_hidden()
        page.evaluate("activeOrator = false; updateViewerCollabBar(viewerCollabEnabled)")
        expect(bar).to_be_visible()
        expect(page.locator("#viewer-collab-bar-status")).to_have_text("Collaboration: On")
        browser.close()
