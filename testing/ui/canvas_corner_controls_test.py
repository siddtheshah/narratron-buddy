"""Check corner packing with the template's real controls and styles."""

from pathlib import Path
import re
import sys

from playwright.sync_api import sync_playwright
import pytest

from testing.ui.canvas_source import read_ui_source


@pytest.mark.parametrize("corner", ["top-left", "bottom-left", "bottom-right"])
def test_visible_corner_controls_pack_against_edges(corner: str) -> None:
    template = read_ui_source(Path("templates/canvas.html"))
    markup = template.split('            <div class="canvas-corner-controls', 1)[1]
    markup = '<div class="canvas-corner-controls' + markup.split('<!-- Image History Paging Controls -->', 1)[0]
    styles = re.search(r"<style>(.*?)</style>", template, re.DOTALL)
    assert styles is not None

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        page.set_content('<div id="image-container">' + markup + '</div>')
        page.locator("#canvas-bookmarks").evaluate('''control => {
            control.innerHTML = '<button id="canvas-bookmark-toggle" aria-label="Bookmarks">1</button>';
        }''')
        for name in ["canvas-navigation", "canvas-bookmarks", "game-renderer"]:
            page.add_style_tag(content=Path(f"static/css/{name}.css").read_text(encoding="utf-8"))
        page.add_style_tag(content=styles.group(1))
        page.add_style_tag(content='''
            #image-container { position: relative; width: 1000px; height: 600px; flex: none; border: 0; }
            * { transition: none !important; }
        ''')
        page.mouse.move(1190, 790)
        group = page.locator(f".canvas-corner-{corner}")
        # Exercise every visibility combination, including role changes and hidden game/prompt controls.
        for mask in range(1, 8):
            group.evaluate('''(group, mask) => {
                [...group.children].forEach((control, index) => {
                    control.hidden = false;
                    control.style.display = mask & (1 << index) ? 'flex' : 'none';
                });
            }''', mask)
            boxes = group.locator(":scope > :visible").evaluate_all('''controls => controls.map(control => {
                const rect = control.getBoundingClientRect();
                return {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom};
            })''')
            canvas = page.locator("#image-container").bounding_box()
            assert canvas is not None
            if corner.endswith("left"):
                assert boxes[0]["left"] == pytest.approx(canvas["x"] + 24)
            else:
                assert boxes[-1]["right"] == pytest.approx(canvas["x"] + canvas["width"] - 24)
            for previous, current in zip(boxes, boxes[1:]):
                assert current["left"] - previous["right"] == pytest.approx(8)
            edge = "top" if corner.startswith("top") else "bottom"
            expected = canvas["y"] + 24 if edge == "top" else canvas["y"] + canvas["height"] - 24
            assert all(box[edge] == pytest.approx(expected) for box in boxes)
        # Expanded hover/click panels cover up subsequent controls without pushing them horizontally.
        boxes_collapsed = group.locator(":scope > :visible").evaluate_all('''controls => controls.map(control => {
            const rect = control.getBoundingClientRect();
            return {left: rect.left, right: rect.right, bottom: rect.bottom};
        })''')
        for control_id in ["prompt-toggle-btn", "suggestions-toggle-btn", "sticky-notes-toggle-btn"]:
            control = group.locator(f"#{control_id}")
            if control.count() == 0:
                continue
            control.evaluate("control => control.classList.add('expanded')")
            boxes = group.locator(":scope > :visible").evaluate_all('''controls => controls.map(control => {
                const rect = control.getBoundingClientRect();
                return {left: rect.left, right: rect.right, bottom: rect.bottom};
            })''')
            control_box = control.evaluate('''el => {
                const rect = el.getBoundingClientRect();
                return {left: rect.left, right: rect.right};
            }''')
            for collapsed, current in zip(boxes_collapsed, boxes):
                assert current["left"] == pytest.approx(collapsed["left"])
                if current["left"] > control_box["left"]:
                    assert control_box["right"] > current["left"]
            control.evaluate("control => control.classList.remove('expanded')")
        browser.close()
