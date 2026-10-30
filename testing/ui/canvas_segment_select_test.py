"""Browser coverage for selecting doodle strokes and stamps with click-drag selection box."""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright


def test_canvas_html_and_renderer_segment_selection_wiring() -> None:
    canvas_html = Path("templates/canvas.html").read_text(encoding="utf-8")
    renderer_js = Path("static/js/canvas-renderers.js").read_text(encoding="utf-8")

    # Renderer support for selectable doodle segments and stamps
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
    assert "getStampsInRect" in renderer_js
    assert "selectStamp" in renderer_js
    assert "getSelectedStamps" in renderer_js
    assert "getTextInRect" in renderer_js
    assert "selectText" in renderer_js
    assert "getSelectedTexts" in renderer_js

    # Selection box marquee
    assert "canvas-selection-box" in renderer_js
    assert "canvas-selection-box" in canvas_html

    # Stroke grouping
    assert "stroke_id" in renderer_js
    assert "stroke_id" in canvas_html
    assert "currentStrokeId" in canvas_html

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
    text_styles = template[
        template.index("        #canvas-text-layer {") : template.index(
            "        .eraser-btn {", template.index("        #canvas-text-layer {")
        )
    ]
    renderer = Path("static/js/canvas-renderers.js").read_text(encoding="utf-8")
    renderer = renderer[renderer.index("export function createDoodleRenderer(") :]

    chat_css = Path("static/css/chat.css").read_text(encoding="utf-8")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="msedge" if sys.platform == "win32" else None
        )
        page = browser.new_page(viewport={"width": 800, "height": 500})
        page.set_content(
            f"<style>{styles} {text_styles} {chat_css} #image-container {{ position: relative; width: 800px; height: 500px; }}</style>"
            '<div id="image-container">'
            '<canvas id="doodle-canvas" style="width: 100%; height: 100%;"></canvas>'
            '<div id="canvas-text-layer"></div>'
            '<div id="canvas-stamp-layer"></div>'
            '<div id="canvas-selection-box" class="canvas-selection-box"></div>'
            "</div>"
        )
        page.add_script_tag(content=renderer.replace("export function", "function"))
        page.add_script_tag(
            content="""
            const canvas = document.getElementById('doodle-canvas');
            const imgContainer = document.getElementById('image-container');
            let isPagedBack = false;
            let selectable = true;
            // Stroke 1 has two connected segments sharing stroke_id 'stroke-1'
            // Stroke 2 has one segment with stroke_id 'stroke-2'
            // Stamp 1 is located at (0.7, 0.2)
            let doodleActions = [
                { id: 'seg-1a', stroke_id: 'stroke-1', type: 'draw', x0: 0.1, y0: 0.2, x1: 0.3, y1: 0.2, color: '#ffffff', size: 4 },
                { id: 'seg-1b', stroke_id: 'stroke-1', type: 'draw', x0: 0.3, y0: 0.2, x1: 0.5, y1: 0.2, color: '#ffffff', size: 4 },
                { id: 'seg-2', stroke_id: 'stroke-2', type: 'draw', x0: 0.1, y0: 0.7, x1: 0.5, y1: 0.7, color: '#ff0000', size: 4 },
                { id: 'stamp-1', type: 'stamp', url: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg"/>', name: 'Stamp 1', x: 0.7, y: 0.2, size: 80, rotation: 0 },
            ];
            window.removedSegments = [];
            window.removedStamps = [];
            const renderer = createDoodleRenderer({
                canvas,
                isVisible: () => true,
                textLayer: document.getElementById('canvas-text-layer'),
                canEditText: () => selectable && !isPagedBack,
                stampLayer: document.getElementById('canvas-stamp-layer'),
                canMoveStamp: () => true,
                onRemoveStamp: (stamp) => {
                    window.removedStamps.push(stamp);
                    doodleActions = doodleActions.filter(a => a.id !== stamp.id);
                    renderer.redraw(doodleActions);
                },
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

            imgContainer.addEventListener('dragover', (e) => {
                if (typeof window !== 'undefined' && window.isCanvasSelectionActive) return;
                const types = e.dataTransfer ? Array.from(e.dataTransfer.types || []) : [];
                const isStampDrag = types.includes('application/json') || types.includes('text/plain');
                if (isStampDrag) {
                    imgContainer.classList.add('stamp-drag-over');
                }
            });
        """
        )
        yield page
        browser.close()


@pytest.mark.parametrize('scale,pan_x,pan_y', [(2, -100, -50), (0.75, 80, 50), (1.5, -120, 30)])
@pytest.mark.parametrize('reverse', [False, True])
def test_selection_box_follows_screen_drag_in_transformed_view(
    segment_page: Page, scale: float, pan_x: int, pan_y: int, reverse: bool,
) -> None:
    page = segment_page
    page.set_viewport_size({'width': 1000, 'height': 700})
    template = Path('templates/canvas.html').read_text(encoding='utf-8')
    box_styles = template[template.index('        .canvas-selection-box {'):template.index('        .canvas-text-editor {')]
    page.add_style_tag(content=box_styles)
    page.add_style_tag(content=Path('static/css/canvas-navigation.css').read_text(encoding='utf-8'))
    page.add_style_tag(content='#image-container { margin: 35px 0 0 25px; border: 3px solid black; }')
    page.locator('#image-container').evaluate('''(container, view) => {
        container.style.setProperty('--canvas-view-scale', view.scale);
        container.style.setProperty('--canvas-view-x', `${view.pan_x}px`);
        container.style.setProperty('--canvas-view-y', `${view.pan_y}px`);
    }''', {'scale': scale, 'pan_x': pan_x, 'pan_y': pan_y})
    scene = page.locator('#doodle-canvas').bounding_box()
    assert scene is not None
    left = scene['x'] + scene['width'] * 0.13
    right = scene['x'] + scene['width'] * 0.52
    top = scene['y'] + scene['height'] * 0.15
    bottom = scene['y'] + scene['height'] * 0.25
    start = (right, bottom) if reverse else (left, top)
    end = (left, top) if reverse else (right, bottom)
    page.mouse.move(*start)
    page.mouse.down()
    page.mouse.move(*end)
    box = page.locator('.canvas-selection-box').bounding_box()
    assert box is not None
    assert box['x'] == pytest.approx(left, abs=0.1)
    assert box['y'] == pytest.approx(top, abs=0.1)
    assert box['width'] == pytest.approx(right - left, abs=0.1)
    assert box['height'] == pytest.approx(bottom - top, abs=0.1)
    page.mouse.up()
    assert page.evaluate('renderer.getSelectedSegments().map(segment => segment.id)') == ['seg-1a', 'seg-1b']
    assert page.evaluate('renderer.getSelectedStamps().length') == 0


def test_doodles_created_in_same_mouse_press_selected_together(segment_page: Page) -> None:
    # Clicking anywhere on seg-1a (e.g. at x=160, y=100) must select BOTH seg-1a and seg-1b because they share stroke-1
    segment_page.mouse.click(160, 100)

    selected_count: int = segment_page.evaluate("() => window.renderer.getSelectedSegments().length")
    assert selected_count == 2

    is_seg1a_selected: bool = segment_page.evaluate("() => window.renderer.isSegmentSelected(window.getDoodleActions()[0])")
    is_seg1b_selected: bool = segment_page.evaluate("() => window.renderer.isSegmentSelected(window.getDoodleActions()[1])")
    is_seg2_selected: bool = segment_page.evaluate("() => window.renderer.isSegmentSelected(window.getDoodleActions()[2])")

    assert is_seg1a_selected is True
    assert is_seg1b_selected is True
    assert is_seg2_selected is False

    # Pressing Delete removes the entire selected stroke (both seg-1a and seg-1b)
    segment_page.keyboard.press("Delete")
    actions_count: int = segment_page.evaluate("() => window.getDoodleActions().filter(a => a.type === 'draw').length")
    assert actions_count == 1
    remaining_id: str = segment_page.evaluate("() => window.getDoodleActions().find(a => a.type === 'draw').id")
    assert remaining_id == "seg-2"


def test_click_drag_selection_box_selects_doodles(segment_page: Page) -> None:
    # Drag a selection box starting at (50, 50) to (450, 400)
    # This encompasses stroke-1 (y=100) and stroke-2 (y=350)
    segment_page.mouse.move(50, 50)
    segment_page.mouse.down()
    segment_page.mouse.move(450, 400)

    # During drag, the selection box must become visible
    is_box_visible: bool = segment_page.evaluate("""() => {
        const el = document.querySelector('.canvas-selection-box');
        return el && el.style.display === 'block';
    }""")
    assert is_box_visible is True

    # Complete the drag
    segment_page.mouse.up()

    # After drag release, the box should be hidden and all 3 segments (both strokes) selected
    is_box_hidden: bool = segment_page.evaluate("""() => {
        const el = document.querySelector('.canvas-selection-box');
        return el && el.style.display === 'none';
    }""")
    assert is_box_hidden is True

    selected_count: int = segment_page.evaluate("() => window.renderer.getSelectedSegments().length")
    assert selected_count == 3


def test_click_drag_selection_box_selects_stamps(segment_page: Page) -> None:
    # Stamp 1 is at x=0.7 * 800 = 560, y=0.2 * 500 = 100 with size 80x80 (from ~520 to 600, ~60 to 140)
    # Drag a selection box around stamp 1 from (500, 40) to (630, 160)
    segment_page.mouse.move(500, 40)
    segment_page.mouse.down()
    segment_page.mouse.move(630, 160)
    segment_page.mouse.up()

    # Stamp 1 should now have the 'movable' selection class
    is_stamp_selected: bool = segment_page.evaluate("""() => {
        const el = document.querySelector('.canvas-stamp-annotation');
        return el && el.classList.contains('movable');
    }""")
    assert is_stamp_selected is True

    selected_stamps_count: int = segment_page.evaluate("() => window.renderer.getSelectedStamps().length")
    assert selected_stamps_count == 1

    # Drag across both doodle stroke-1 and stamp 1 from (50, 40) to (640, 160)
    segment_page.mouse.move(50, 40)
    segment_page.mouse.down()
    segment_page.mouse.move(640, 160)
    segment_page.mouse.up()

    # Both stroke-1 (2 segments) and stamp 1 should be selected
    selected_segments: int = segment_page.evaluate("() => window.renderer.getSelectedSegments().length")
    assert selected_segments == 2
    is_stamp_still_selected: bool = segment_page.evaluate("""() => {
        const el = document.querySelector('.canvas-stamp-annotation');
        return el && el.classList.contains('movable');
    }""")
    assert is_stamp_still_selected is True


def test_selection_box_drag_across_center_does_not_highlight_frame(segment_page: Page) -> None:
    # Start drag at x=200, y=250 and drag across center (width=800 -> center is x=400) to x=600, y=250
    segment_page.mouse.move(200, 250)
    segment_page.mouse.down()
    segment_page.mouse.move(400, 250)
    segment_page.mouse.move(600, 250)

    # During drag across center line, #image-container must not receive stamp-drag-over class
    is_drag_over: bool = segment_page.evaluate("""() => {
        const el = document.getElementById('image-container');
        return el ? el.classList.contains('stamp-drag-over') : false;
    }""")
    assert is_drag_over is False

    # Simulate accidental dragover event dispatch during selection drag
    segment_page.evaluate("""() => {
        const el = document.getElementById('image-container');
        const event = new Event('dragover', { bubbles: true, cancelable: true });
        el.dispatchEvent(event);
    }""")
    is_drag_over_after_event: bool = segment_page.evaluate("""() => {
        const el = document.getElementById('image-container');
        return el ? el.classList.contains('stamp-drag-over') : false;
    }""")
    assert is_drag_over_after_event is False

    segment_page.mouse.up()


def test_clicking_off_stamp_drops_selection(segment_page: Page) -> None:
    # Select stamp 1 by left-click-drag box
    segment_page.mouse.move(500, 40)
    segment_page.mouse.down()
    segment_page.mouse.move(630, 160)
    segment_page.mouse.up()

    assert segment_page.evaluate("() => window.renderer.getSelectedStamps().length") == 1
    assert segment_page.evaluate("""() => {
        const el = document.querySelector('.canvas-stamp-annotation');
        return el && el.classList.contains('movable') && document.activeElement === el;
    }""") is True

    # Click off the stamp onto empty canvas
    segment_page.mouse.click(200, 200)

    # Stamp selection must be dropped (both movable class, selected state, and focus)
    selected_count: int = segment_page.evaluate("() => window.renderer.getSelectedStamps().length")
    is_stamp_movable: bool = segment_page.evaluate("""() => {
        const el = document.querySelector('.canvas-stamp-annotation');
        return el && el.classList.contains('movable');
    }""")
    has_focus: bool = segment_page.evaluate("""() => {
        const el = document.querySelector('.canvas-stamp-annotation');
        return document.activeElement === el;
    }""")

    assert selected_count == 0
    assert is_stamp_movable is False
    assert has_focus is False

    # Pressing Delete after dropping selection must NOT delete the stamp
    segment_page.keyboard.press("Delete")
    remaining_stamps: int = segment_page.evaluate("() => window.getDoodleActions().filter(a => a.type === 'stamp').length")
    assert remaining_stamps == 1


def test_selection_box_multiple_stamps_and_doodles_deletion(segment_page: Page) -> None:
    # Add a second stamp alongside the initial stamp and doodle strokes
    segment_page.evaluate("""() => {
        const action = {
            id: 'stamp-2',
            type: 'stamp',
            url: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg"/>',
            name: 'Stamp 2',
            x: 0.85,
            y: 0.2,
            size: 80,
            rotation: 0
        };
        window.getDoodleActions().push(action);
        window.renderer.redraw(window.getDoodleActions());
    }""")

    # Verify initial actions: 2 stamps, 3 doodle segments (2 in stroke-1, 1 in stroke-2)
    initial_stamps: int = segment_page.evaluate("() => window.getDoodleActions().filter(a => a.type === 'stamp').length")
    assert initial_stamps == 2
    initial_doodles: int = segment_page.evaluate("() => window.getDoodleActions().filter(a => a.type === 'draw').length")
    assert initial_doodles == 3

    # Drag selection box across stroke-1 (x=0.1..0.5, y=0.2) and both stamps (x=0.7 and 0.85, y=0.2)
    segment_page.mouse.move(50, 40)
    segment_page.mouse.down()
    segment_page.mouse.move(760, 160)
    segment_page.mouse.up()

    # Verify both stamps and both segments of stroke-1 are selected
    selected_stamps_count: int = segment_page.evaluate("() => window.renderer.getSelectedStamps().length")
    assert selected_stamps_count == 2
    selected_segments_count: int = segment_page.evaluate("() => window.renderer.getSelectedSegments().length")
    assert selected_segments_count == 2

    # Press Delete key to remove all selected items
    segment_page.keyboard.press("Delete")

    # ALL stamps must be deleted
    remaining_stamps: int = segment_page.evaluate("() => window.getDoodleActions().filter(a => a.type === 'stamp').length")
    assert remaining_stamps == 0
    removed_stamps_count: int = segment_page.evaluate("() => window.removedStamps.length")
    assert removed_stamps_count == 2

    # Selected doodle stroke-1 must be deleted, unselected stroke-2 must remain
    remaining_doodles: int = segment_page.evaluate("() => window.getDoodleActions().filter(a => a.type === 'draw').length")
    assert remaining_doodles == 1
    remaining_doodle_id: str = segment_page.evaluate("() => window.getDoodleActions().find(a => a.type === 'draw').id")
    assert remaining_doodle_id == "seg-2"


def test_selection_box_multiple_stamps_deletion_without_doodles(segment_page: Page) -> None:
    # Add a second stamp
    segment_page.evaluate("""() => {
        const action = {
            id: 'stamp-2',
            type: 'stamp',
            url: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg"/>',
            name: 'Stamp 2',
            x: 0.85,
            y: 0.2,
            size: 80,
            rotation: 0
        };
        window.getDoodleActions().push(action);
        window.renderer.redraw(window.getDoodleActions());
    }""")

    # Drag selection box around ONLY the two stamps (x=500..760, y=40..160)
    segment_page.mouse.move(500, 40)
    segment_page.mouse.down()
    segment_page.mouse.move(760, 160)
    segment_page.mouse.up()

    # Verify both stamps are selected, and no doodle segments are selected
    selected_stamps_count: int = segment_page.evaluate("() => window.renderer.getSelectedStamps().length")
    assert selected_stamps_count == 2
    selected_segments_count: int = segment_page.evaluate("() => window.renderer.getSelectedSegments().length")
    assert selected_segments_count == 0

    # Press Delete key to remove selected stamps
    segment_page.keyboard.press("Delete")

    # ALL stamps must be deleted
    remaining_stamps: int = segment_page.evaluate("() => window.getDoodleActions().filter(a => a.type === 'stamp').length")
    assert remaining_stamps == 0

    # All doodles must remain intact
    remaining_doodles: int = segment_page.evaluate("() => window.getDoodleActions().filter(a => a.type === 'draw').length")
    assert remaining_doodles == 3




def test_click_drag_selection_box_selects_text_items(segment_page: Page) -> None:
    # Add a text annotation at x=0.2 * 800 = 160, y=0.5 * 500 = 250
    segment_page.evaluate("""() => {
        const action = {
            id: 'text-1',
            type: 'text',
            text: 'Secret Clue',
            x: 0.2,
            y: 0.5,
            size: 32,
            color: '#ffffff',
            font: 'Outfit'
        };
        window.getDoodleActions().push(action);
        window.renderer.redraw(window.getDoodleActions());
    }""")

    # Verify text element rendered
    label = segment_page.locator('.canvas-text-annotation')
    assert label.inner_text() == 'Secret Clue'
    assert label.evaluate("el => el.classList.contains('movable')") is False

    # Drag a selection box around the text item from (120, 220) to (320, 300)
    segment_page.mouse.move(120, 220)
    segment_page.mouse.down()
    segment_page.mouse.move(320, 300)
    segment_page.mouse.up()

    # The text item must now have 'movable' class and be in getSelectedTexts()
    assert label.evaluate("el => el.classList.contains('movable')") is True
    selected_texts_count: int = segment_page.evaluate("() => window.renderer.getSelectedTexts().length")
    assert selected_texts_count == 1
    selected_id: str = segment_page.evaluate("() => window.renderer.getSelectedTexts()[0].id")
    assert selected_id == 'text-1'

    # Strokes and stamps outside the box must not be selected
    assert segment_page.evaluate("() => window.renderer.getSelectedSegments().length") == 0
    assert segment_page.evaluate("() => window.renderer.getSelectedStamps().length") == 0


def test_click_drag_selection_box_selects_doodles_stamps_and_text_together(segment_page: Page) -> None:
    # Add text annotation at x=0.35, y=0.15
    segment_page.evaluate("""() => {
        const action = {
            id: 'text-1',
            type: 'text',
            text: 'Joint Selection',
            x: 0.35,
            y: 0.15,
            size: 24,
            color: '#ffffff',
            font: 'Outfit'
        };
        window.getDoodleActions().push(action);
        window.renderer.redraw(window.getDoodleActions());
    }""")

    # Drag selection box across stroke-1 (x=0.1..0.5, y=0.2), text-1 (x=0.35, y=0.15), and stamp-1 (x=0.7, y=0.2)
    segment_page.mouse.move(50, 40)
    segment_page.mouse.down()
    segment_page.mouse.move(650, 160)
    segment_page.mouse.up()

    # All three types must be captured by the selection box
    selected_segments: int = segment_page.evaluate("() => window.renderer.getSelectedSegments().length")
    selected_stamps: int = segment_page.evaluate("() => window.renderer.getSelectedStamps().length")
    selected_texts: int = segment_page.evaluate("() => window.renderer.getSelectedTexts().length")

    assert selected_segments == 2
    assert selected_stamps == 1
    assert selected_texts == 1

    # Clicking off onto empty canvas drops all selections
    segment_page.mouse.click(200, 400)
    assert segment_page.evaluate("() => window.renderer.getSelectedSegments().length") == 0
    assert segment_page.evaluate("() => window.renderer.getSelectedStamps().length") == 0
    assert segment_page.evaluate("() => window.renderer.getSelectedTexts().length") == 0


def test_escape_key_drops_text_selection(segment_page: Page) -> None:
    # Add text annotation
    segment_page.evaluate("""() => {
        const action = {
            id: 'text-esc',
            type: 'text',
            text: 'Escape me',
            x: 0.2,
            y: 0.5,
            size: 32,
            color: '#ffffff',
            font: 'Outfit'
        };
        window.getDoodleActions().push(action);
        window.renderer.redraw(window.getDoodleActions());
    }""")

    # Drag-select the text
    segment_page.mouse.move(120, 220)
    segment_page.mouse.down()
    segment_page.mouse.move(320, 300)
    segment_page.mouse.up()

    assert segment_page.evaluate("() => window.renderer.getSelectedTexts().length") == 1

    # Press Escape key
    segment_page.keyboard.press("Escape")

    assert segment_page.evaluate("() => window.renderer.getSelectedTexts().length") == 0
    is_movable: bool = segment_page.evaluate("""() => {
        const el = document.querySelector('.canvas-text-annotation');
        return el ? el.classList.contains('movable') : false;
    }""")
    assert is_movable is False
