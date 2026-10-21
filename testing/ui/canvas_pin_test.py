"""Regression coverage for the orator canvas pin control."""

from pathlib import Path

from playwright.sync_api import expect, sync_playwright
import pytest


@pytest.mark.parametrize("visual_pinned,music_pinned", [(False, False), (True, False), (False, True), (True, True)])
def test_canvas_pin_button_toggles_image_and_music(visual_pinned: bool, music_pinned: bool) -> None:
    content = Path("templates/canvas.html").read_text(encoding="utf-8")
    pin_script = content.split("        function updateCanvasPinButton() {", 1)[1].split(
        "        const actionWheel = initializeActionWheel({", 1
    )[0]
    pin_script = "function updateCanvasPinButton() {" + pin_script

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.set_content('<button id="canvas-pin-btn"><span id="canvas-pin-label"></span></button>')
        page.evaluate("""([visualPinned, audioPinned]) => {
            window.canvasPinBtn = document.getElementById('canvas-pin-btn');
            window.canvasPinLabel = document.getElementById('canvas-pin-label');
            window.canvasPinned = visualPinned;
            window.musicPinned = audioPinned;
            window.isCurrentOrator = () => true;
            window.theaterId = 'stage';
            window.actionWheel = { updateState: (...pins) => { window.wheelPins = pins; } };
            window.fetch = async (url, options) => {
                window.pinRequest = { url, ...JSON.parse(options.body) };
                return { ok: true, json: async () => ({
                    pinned: window.pinRequest.pinned, music_pinned: window.pinRequest.pinned,
                }) };
            };
        }""", [visual_pinned, music_pinned])
        page.add_script_tag(content=pin_script + "\nupdateCanvasPinButton();")
        button = page.locator('#canvas-pin-btn')
        pressed = "mixed" if visual_pinned != music_pinned else str(visual_pinned).lower()
        expect(button).to_have_attribute('aria-pressed', pressed)

        button.click()

        expected = not (visual_pinned and music_pinned)
        expect(button).to_have_attribute('aria-pressed', str(expected).lower())
        expect(button).to_have_class('active' if expected else '')
        expect(button).to_have_attribute('title', (
            'Unpin image and music and allow changes' if expected
            else 'Pin the current image and music and block changes'
        ))
        assert page.evaluate('window.pinRequest') == {
            'url': '/api/theaters/stage/canvas-pin', 'pinned': expected,
        }
        assert page.evaluate('window.wheelPins') == [expected, expected]
        browser.close()
