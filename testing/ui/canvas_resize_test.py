"""Verify the canvas stays dark throughout chat panel layout transitions."""

import re
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def test_canvas_reveals_final_layout_after_chat_slide_and_window_resize() -> None:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    styles = re.search(r"<style>(.*?)</style>", template, re.DOTALL)
    assert styles is not None
    start = template.index("        // Cover intermediate layouts")
    end = template.index("        if (popoutCollapseBtn)", start)
    script = """
        let userToggledPanel = false;
        let isPanelCollapsed = false;
        const popoutExpandBtn = null;
        window.drawnSizes = [];
        function resizeRendererCanvas() {
            const rect = document.getElementById('image-container').getBoundingClientRect();
            window.drawnSizes.push(rect.width);
        }
        function resizeDoodleCanvas() {}
    """ + template[start:end]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 1500, "height": 800})
        page.set_content("""
            <div id="main-content"><div id="image-container"></div></div>
            <div id="chat-sidebar"></div>
        """)
        page.add_style_tag(content=styles.group(1))
        page.add_style_tag(content="""
            body { display: flex; }
            #main-content { flex: 1; min-width: 0; display: flex; }
        """)
        page.add_script_tag(content=script)
        canvas = page.locator("#image-container")
        expect(canvas).not_to_have_class("layout-resizing")

        page.evaluate("setPanelCollapsed(true, true)")
        expect(canvas).to_have_class("layout-resizing")
        assert page.evaluate("""() => {
            const cover = getComputedStyle(document.getElementById('image-container'), '::after');
            return cover.backgroundColor === 'rgb(2, 6, 23)' && cover.content !== 'none';
        }""")
        # Reverse the slide before it finishes; the cover must survive both motions.
        page.wait_for_timeout(150)
        expect(canvas).to_have_class("layout-resizing")
        page.evaluate("setPanelCollapsed(false, true)")
        page.wait_for_timeout(200)
        expect(canvas).to_have_class("layout-resizing")
        expect(canvas).not_to_have_class("layout-resizing", timeout=2000)
        assert page.evaluate("""() => window.drawnSizes.at(-1) ===
            document.getElementById('image-container').getBoundingClientRect().width""")

        page.set_viewport_size({"width": 1400, "height": 800})
        expect(canvas).to_have_class("layout-resizing")
        expect(canvas).not_to_have_class("layout-resizing", timeout=2000)
        assert page.evaluate("""() => window.drawnSizes.at(-1) ===
            document.getElementById('image-container').getBoundingClientRect().width""")
        browser.close()
