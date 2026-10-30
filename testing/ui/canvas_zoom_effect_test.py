"""Keep centered image effects registered with canvas pixels during navigation."""

from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from playwright.sync_api import Route, sync_playwright
import pytest


@pytest.mark.parametrize("source_size", [(400, 400), (800, 200)])
@pytest.mark.parametrize("fit_mode", ["contain", "cover"])
def test_zoom_keeps_image_effect_aligned(
    source_size: tuple[int, int], fit_mode: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    template = (root / "templates/canvas.html").read_text(encoding="utf-8")
    styles = re.search(r"<style>(.*?)</style>", template, re.DOTALL)
    assert styles is not None

    def serve_script(route: Route) -> None:
        path = urlsplit(route.request.url).path
        route.fulfill(path=str(root / path.lstrip("/")), content_type="text/javascript")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(device_scale_factor=2)
        page.route("https://canvas.test/static/js/**", serve_script)
        page.set_content('''
            <div id="image-container">
                <canvas id="canvas-renderer"></canvas><img id="current-image">
            </div>
        ''')
        page.add_style_tag(content=styles.group(1))
        page.add_style_tag(content=(root / "static/css/image-effects.css").read_text(encoding="utf-8"))
        page.add_style_tag(content=(root / "static/css/canvas-navigation.css").read_text(encoding="utf-8"))
        page.add_style_tag(content='''
            #image-container { position: relative; flex: none; width: 600px; height: 400px; }
            #canvas-renderer { position: absolute; inset: 0; width: 100%; height: 100%; }
        ''')
        page.evaluate('''async ({width, height, fitMode}) => {
            const {createImageRenderer} = await import('https://canvas.test/static/js/canvas-renderers.js');
            const canvas = document.getElementById('canvas-renderer');
            window.scene = createImageRenderer({canvas, image: document.getElementById('current-image'), fitMode});
            scene.resize();
            const source = document.createElement('canvas');
            source.width = width; source.height = height;
            const ctx = source.getContext('2d');
            ctx.fillStyle = '#00ff00'; ctx.fillRect(0, 0, width, height);
            ctx.fillStyle = '#ff0000'; ctx.fillRect(0, 0, width / 2, height);
            await scene.applyTransition(source.toDataURL(), 'none', 'sparkle');
            await document.getElementById('current-image').decode();
        }''', {"width": source_size[0], "height": source_size[1], "fitMode": fit_mode})

        # Include zoom out, zoom in, panning, incoming redraws, and a layout change.
        for scale, width in [(1, 600), (0.5, 600), (2, 600), (2, 500), (1, 500)]:
            page.evaluate('''({scale, width}) => {
                const container = document.getElementById('image-container');
                container.style.width = `${width}px`;
                container.style.setProperty('--canvas-view-scale', scale);
                container.style.setProperty('--canvas-view-x', '37px');
                container.style.setProperty('--canvas-view-y', '-23px');
                scene.resize();
            }''', {"scale": scale, "width": width})
            canvas_box = page.locator("#canvas-renderer").bounding_box()
            effect_box = page.locator(".canvas-image-effect-frame").bounding_box()
            assert canvas_box is not None
            assert effect_box is not None
            ratios = (width / source_size[0], 400 / source_size[1])
            fit = min(ratios) if fit_mode == "contain" else max(ratios)
            drawn_width, drawn_height = source_size[0] * fit, source_size[1] * fit
            assert effect_box == pytest.approx({
                "x": canvas_box["x"] + (width - drawn_width) / 2 * scale,
                "y": canvas_box["y"] + (400 - drawn_height) / 2 * scale,
                "width": drawn_width * scale,
                "height": drawn_height * scale,
            }, abs=0.1)
        browser.close()
