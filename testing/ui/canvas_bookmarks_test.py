"""Exercise bookmark controls with real DOM events and browser storage."""

from pathlib import Path
import sys

from playwright.sync_api import expect, sync_playwright


def test_bookmarks_toggle_hover_navigation_and_persistence(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 800, "height": 600})
        page.route("https://canvas.test/", lambda route: route.fulfill(
            body='<div id="canvas-bookmarks" hidden></div>', content_type="text/html"
        ))
        page.route("https://canvas.test/bookmarks.js", lambda route: route.fulfill(
            path=str(root / "static/js/canvas-bookmarks.js"), content_type="text/javascript"
        ))
        page.goto("https://canvas.test/")
        page.add_style_tag(content=(root / "static/css/canvas-bookmarks.css").read_text(encoding="utf-8"))
        setup = '''async () => {
            const { initializeCanvasBookmarks } = await import('/bookmarks.js');
            window.historyItems = [
                {path: 'images/Ancient_forest.png', time: 1},
                {path: 'images/A_very_long_image_name_that_should_be_truncated_in_the_panel.png', time: 2},
                {path: 'images/Ancient_forest.png', time: 3}
            ];
            window.index = 0;
            window.orator = true;
            window.failNavigation = false;
            window.bookmarks = initializeCanvasBookmarks({
                theaterId: 'test', isOrator: () => orator,
                getHistory: () => historyItems, getIndex: () => index,
                navigate: async target => { if (failNavigation) throw new Error('Rejected'); index = target; }
            });
        }'''
        page.evaluate(setup)
        toggle = page.locator("#canvas-bookmark-toggle")
        panel = page.locator("#canvas-bookmark-panel")
        rows = page.locator(".bookmark-item")
        expect(panel).to_be_hidden()
        toggle.hover()
        expect(panel).to_be_visible()
        toggle.click()
        expect(toggle).to_have_attribute("aria-pressed", "true")
        expect(page.locator(".bookmark-flash")).to_have_text("1")
        first_color = page.locator(".bookmark-number").evaluate("el => getComputedStyle(el).color")
        page.mouse.move(700, 500)
        expect(panel).to_be_hidden()
        toggle.hover()
        expect(panel).to_be_visible()
        page.evaluate("index = 1; bookmarks.refresh()")
        toggle.click()
        expect(rows).to_have_count(2)
        assert rows.nth(1).locator(".bookmark-name").inner_text().endswith("…")
        assert rows.nth(1).locator(".bookmark-number").evaluate("el => getComputedStyle(el).color") != first_color
        page.evaluate("index = 2; bookmarks.refresh()")
        expect(toggle).to_have_attribute("aria-pressed", "false")
        toggle.click()
        expect(rows).to_have_count(3)
        rows.first.click()
        assert page.evaluate("index") == 0
        expect(toggle).to_be_focused()
        expect(panel).to_be_hidden()
        toggle.click()
        expect(rows).to_have_count(2)
        expect(rows.first.locator(".bookmark-number")).to_have_text("2")
        expect(page.locator(".bookmark-status")).to_have_text("Bookmark 1 removed.")
        # A failed jump leaves the current page unchanged and gives feedback.
        page.evaluate("failNavigation = true")
        rows.first.click()
        expect(page.locator(".bookmark-status")).to_have_text("Could not open bookmark. Try again.")
        assert page.evaluate("index") == 0
        # Persist through a real reload and exercise keyboard navigation.
        page.reload()
        page.add_style_tag(content=(root / "static/css/canvas-bookmarks.css").read_text(encoding="utf-8"))
        page.evaluate(setup)
        toggle.focus()
        toggle.press("ArrowDown")
        expect(rows.first).to_be_focused()
        expect(rows).to_have_count(2)
        rows.first.press("Enter")
        assert page.evaluate("index") == 1
        expect(toggle).to_have_attribute("aria-pressed", "true")
        toggle.press("Escape")
        expect(panel).to_be_hidden()
        # Missing history entries stay out of the panel; appending retains identities.
        page.evaluate("historyItems.pop(); historyItems.push({path: 'new.png', time: 4}); bookmarks.refresh()")
        toggle.hover()
        expect(rows).to_have_count(1)
        page.screenshot(path=str(tmp_path / "canvas-bookmarks.png"))
        page.evaluate("orator = false; bookmarks.refresh()")
        expect(page.locator("#canvas-bookmarks")).to_be_hidden()
        browser.close()
