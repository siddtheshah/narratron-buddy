"""Browser coverage for selecting, multi-selecting, and removing canvas doodle sketch segments."""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright


def test_canvas_html_and_renderer_segment_selection_wiring() -> None:
    canvas_html = Path("templates/canvas.html").read_text(encoding="utf-8")
    renderer_js = Path("static/js/canvas-renderers.js").read_text(encoding="utf-8")

    # Renderer support for selectable doodle segments
    assert "canSelectSegment" in renderer_js
    assert "onSelectSegment" in renderer_js
    assert "onRemoveSegment" in renderer_js
    assert "selectSegment" in renderer_js
    assert "deselectSegment" in renderer_js
    assert "clearSelectedSegments" in renderer_js
    assert "getSelectedSegments" in renderer_js
    assert "isSegmentSelected" in renderer_js
    assert "findSegmentAt" in renderer_js
    assert "getSegmentsInRect" in renderer_js
    assert "selectSegmentsInRect" in renderer_js

    # Selection highlight styling in renderSegment
    assert "isSelected" in renderer_js
    assert "#818cf8" in renderer_js

    # Canvas.html wiring
    assert "canSelectSegment:" in canvas_html
    assert "onRemoveSegment:" in canvas_html


@pytest.fixture
def segment_page() -> Iterator[Page]:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    styles = template[
        template.index("        #doodle-canvas {") : template.index(
            "        .status-dot {", template.index("        #doodle-canvas {")
        )
    ]
    renderer = Path("static/js/canvas-renderers.js").read_text(encoding="utf-8")
    renderer = renderer[renderer.index("export function createDoodleRenderer(") :]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="msedge" if sys.platform == "win32" else None
        )
        page = browser.new_page(viewport={"width": 800, "height": 500})
        page.set_content(
            f"<style>{styles} #image-container {{ position: relative; width: 800px; height: 500px; }}</style>"
            '<div id="image-container">'
            '<canvas id="doodle-canvas" style="width: 100%; height: 100%;"></canvas>'
            '<div id="canvas-text-layer"></div>'
            '<div id="canvas-stamp-layer"></div>'
            "</div>"
        )
        page.add_script_tag(content=renderer.replace("export function", "function"))
        page.add_script_tag(
            content="""
            const canvas = document.getElementById('doodle-canvas');
            const imgContainer = document.getElementById('image-container');
            let isPagedBack = false;
            let selectable = true;
            let doodleActions = [
                { id: 'seg-1', type: 'draw', x0: 0.2, y0: 0.2, x1: 0.8, y1: 0.2, color: '#ffffff', size: 4 },
                { id: 'seg-2', type: 'draw', x0: 0.2, y0: 0.6, x1: 0.8, y1: 0.6, color: '#ff0000', size: 4 },
            ];
            window.removedSegments = [];
            const renderer = createDoodleRenderer({
                canvas,
                isVisible: () => true,
                canSelectSegment: () => selectable && !isPagedBack,
                onRemoveSegment: (removed) => {
                    window.removedSegments.push(...removed);
                    const removedSet = new Set(removed.map(r => r.id || r));
                    doodleActions = doodleActions.filter(a => !removedSet.has(a.id) && !removedSet.has(a));
                    renderer.redraw(doodleActions);
                },
            });
            window.renderer = renderer;
            window.getDoodleActions = () => doodleActions;
            renderer.resize(doodleActions);
        """
        )
        yield page
        browser.close()


def test_select_multi_select_escape_and_delete_segments(segment_page: Page) -> None:
    # Segment 1 is at y0=0.2, y1=0.2 on height=500 -> y=100. x spans 0.2 to 0.8 -> 160 to 640.
    # Click at (400, 100) to select segment 1
    segment_page.mouse.click(400, 100)

    selected_count: int = segment_page.evaluate("() => window.renderer.getSelectedSegments().length")
    assert selected_count == 1
    is_seg1_selected: bool = segment_page.evaluate("() => window.renderer.isSegmentSelected(window.getDoodleActions()[0])")
    assert is_seg1_selected is True

    # Shift-click at (400, 300) to multi-select segment 2 (y=0.6 * 500 = 300)
    segment_page.keyboard.down("Shift")
    segment_page.mouse.click(400, 300)
    segment_page.keyboard.up("Shift")

    selected_count = segment_page.evaluate("() => window.renderer.getSelectedSegments().length")
    assert selected_count == 2

    # Click empty space at (50, 50) without Shift to deselect all
    segment_page.mouse.click(50, 50)
    selected_count = segment_page.evaluate("() => window.renderer.getSelectedSegments().length")
    assert selected_count == 0

    # Click segment 1 again and press Escape to deselect
    segment_page.mouse.click(400, 100)
    selected_count = segment_page.evaluate("() => window.renderer.getSelectedSegments().length")
    assert selected_count == 1
    segment_page.keyboard.press("Escape")
    selected_count = segment_page.evaluate("() => window.renderer.getSelectedSegments().length")
    assert selected_count == 0

    # Click segment 1 and press Delete to remove it
    segment_page.mouse.click(400, 100)
    selected_count = segment_page.evaluate("() => window.renderer.getSelectedSegments().length")
    assert selected_count == 1
    segment_page.keyboard.press("Delete")

    actions_count: int = segment_page.evaluate("() => window.getDoodleActions().length")
    assert actions_count == 1
    remaining_id: str = segment_page.evaluate("() => window.getDoodleActions()[0].id")
    assert remaining_id == "seg-2"
    selected_count = segment_page.evaluate("() => window.renderer.getSelectedSegments().length")
    assert selected_count == 0
