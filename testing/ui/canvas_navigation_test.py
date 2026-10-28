"""Exercise viewer navigation with the real image and annotation renderers."""

from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from playwright.sync_api import Route, expect, sync_playwright
import pytest


def test_navigation_keeps_rendered_content_aligned_and_resets_locally() -> None:
    root = Path(__file__).resolve().parents[2]
    template = (root / "templates/canvas.html").read_text(encoding="utf-8")
    controls = re.search(
        r'(<div id="canvas-navigation-surface".*?</button>)', template, re.DOTALL
    )
    assert controls is not None

    def serve_script(route: Route) -> None:
        path = urlsplit(route.request.url).path
        route.fulfill(path=str(root / path.lstrip("/")), content_type="text/javascript")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 1000, "height": 800})
        page.route("https://canvas.test/static/js/**", serve_script)
        page.set_content('''
            <div id="image-container">
                <canvas id="canvas-renderer"></canvas><img id="current-image" hidden>
                <div id="canvas-stamp-layer"></div><canvas id="doodle-canvas"></canvas>
                <div id="canvas-text-layer"></div>
            ''' + controls.group(1) + "</div>")
        page.add_style_tag(content='''
            #image-container { position: relative; width: 600px; height: 400px; overflow: hidden; }
            canvas, #canvas-stamp-layer, #canvas-text-layer { position: absolute; inset: 0; width: 100%; height: 100%; }
            .canvas-stamp-annotation { position: absolute; }
        ''')
        page.add_style_tag(content=(root / "static/css/canvas-navigation.css").read_text(encoding="utf-8"))
        page.add_script_tag(content=(root / "static/js/canvas-navigation.js").read_text(encoding="utf-8"))
        page.evaluate('''async () => {
            const {createImageRenderer, createDoodleRenderer} = await import('https://canvas.test/static/js/canvas-renderers.js');
            const canvas = document.getElementById('canvas-renderer');
            window.scene = createImageRenderer({canvas, image: document.getElementById('current-image')});
            scene.resize();
            const source = document.createElement('canvas');
            source.width = 600; source.height = 400;
            const ctx = source.getContext('2d');
            ctx.fillStyle = '#00ff00'; ctx.fillRect(0, 0, 600, 400);
            await scene.applyTransition(source.toDataURL(), 'none', 'none');
            window.actions = [
                {type: 'draw', id: 'line', x0: .2, y0: .25, x1: .3, y1: .25, color: '#ff0000', size: 6},
                {type: 'stamp', id: 'stamp', x: .5, y: .5, size: 100, url: source.toDataURL()}
            ];
            window.annotations = createDoodleRenderer({
                canvas: document.getElementById('doodle-canvas'),
                stampLayer: document.getElementById('canvas-stamp-layer')
            });
            annotations.resize(actions);
            window.originalActions = JSON.stringify(actions);
        }''')
        button = page.get_by_role("button", name="Zoom and pan")
        surface = page.locator("#canvas-navigation-surface")
        stamp = page.locator(".canvas-stamp-annotation")
        original_stamp = stamp.bounding_box()
        assert original_stamp is not None
        expect(surface).to_be_hidden()
        # Disabled mode leaves wheel input alone.
        page.mouse.move(208, 158)
        page.mouse.wheel(0, -400)
        assert page.locator("#doodle-canvas").bounding_box()["width"] == 600
        button.click()
        expect(button).to_have_attribute("aria-pressed", "true")
        page.mouse.move(208, 158)
        page.mouse.wheel(0, -400)
        page.wait_for_function("Number(document.getElementById('image-container').style.getPropertyValue('--canvas-view-scale')) > 1")
        scale = page.evaluate("Number(document.getElementById('image-container').style.getPropertyValue('--canvas-view-scale'))")
        zoomed = stamp.bounding_box()
        assert zoomed is not None
        assert zoomed["width"] == pytest.approx(original_stamp["width"] * scale, abs=0.1)
        # Both canvases and stamps share a transform, including after incoming updates.
        page.evaluate("annotations.redraw(actions); scene.resize(); annotations.resize(actions)")
        assert stamp.bounding_box()["width"] == pytest.approx(zoomed["width"], abs=0.1)
        assert page.evaluate("document.getElementById('doodle-canvas').width") == 600
        assert page.evaluate("Array.from(document.getElementById('doodle-canvas').getContext('2d').getImageData(150,100,1,1).data)") == [255, 0, 0, 255]
        assert page.evaluate("Array.from(document.getElementById('canvas-renderer').getContext('2d').getImageData(599,399,1,1).data)") == [0, 255, 0, 255]
        page.mouse.move(208, 158)
        page.mouse.down()
        page.mouse.move(248, 188)
        page.mouse.up()
        moved = stamp.bounding_box()
        assert moved["x"] - zoomed["x"] == pytest.approx(40, abs=0.1)
        assert moved["y"] - zoomed["y"] == pytest.approx(30, abs=0.1)
        assert page.evaluate("JSON.stringify(actions) === originalActions")
        assert page.evaluate("annotations.getSelectedSegments().length") == 0
        # Real touch pointers exercise pinch and cancellation without emulating a wheel.
        touch = page.context.new_cdp_session(page)
        touch.send("Input.dispatchTouchEvent", {
            "type": "touchStart", "touchPoints": [
                {"x": 200, "y": 200, "id": 1}, {"x": 300, "y": 200, "id": 2}
            ],
        })
        touch.send("Input.dispatchTouchEvent", {
            "type": "touchMove", "touchPoints": [
                {"x": 150, "y": 200, "id": 1}, {"x": 350, "y": 200, "id": 2}
            ],
        })
        touch.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
        assert page.evaluate("Number(document.getElementById('image-container').style.getPropertyValue('--canvas-view-scale'))") == pytest.approx(scale * 2)
        expect(surface).not_to_have_class("dragging")
        surface.dblclick(position={"x": 100, "y": 100})
        assert page.locator("#doodle-canvas").bounding_box()["width"] == 600
        page.mouse.move(208, 158)
        page.mouse.wheel(0, -100000)
        page.wait_for_function("document.getElementById('image-container').style.getPropertyValue('--canvas-view-scale') === '5'")
        page.mouse.wheel(0, 100000)
        page.wait_for_function("document.getElementById('image-container').style.getPropertyValue('--canvas-view-scale') === '0.5'")
        button.click()
        expect(surface).to_be_hidden()
        expect(button).to_have_attribute("aria-pressed", "false")
        assert stamp.bounding_box()["width"] == pytest.approx(original_stamp["width"], abs=0.1)
        assert page.locator("#doodle-canvas").bounding_box()["width"] == 600
        browser.close()
