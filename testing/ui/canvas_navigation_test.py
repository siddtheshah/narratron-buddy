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
            ''' + controls.group(1) + '''</div>
            <button class="color-btn" data-color="#ff0000">Red marker</button>
            <button class="color-btn" data-color="#00ff00">Green marker</button>
            <button id="text-annotation-toggle">Text tool</button>
            ''')
        page.add_style_tag(content='''
            #image-container { position: relative; width: 600px; height: 400px; overflow: hidden; }
            canvas, #canvas-stamp-layer, #canvas-text-layer { position: absolute; inset: 0; width: 100%; height: 100%; }
            #canvas-text-layer, #canvas-stamp-layer { pointer-events: none; }
            .canvas-stamp-annotation { position: absolute; pointer-events: auto; }
            .canvas-stamp-annotation img { width: 100%; height: 100%; }
        ''')
        page.add_style_tag(content=(root / "static/css/canvas-navigation.css").read_text(encoding="utf-8"))
        page.evaluate('''async () => {
            window.canvasCursorState = (await import('https://canvas.test/static/js/canvas-cursor-state.js?v=20261028-1')).canvasCursorState;
            await import('https://canvas.test/static/js/canvas-navigation.js');
        }''')
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
                stampLayer: document.getElementById('canvas-stamp-layer'),
                canSelectSegment: () => canvasCursorState.current.type === 'select',
                canMoveStamp: () => canvasCursorState.current.type === 'select',
                onMoveStamp: action => { actions[1] = action; annotations.redraw(actions); }
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
        # Selecting a marker exits navigation without changing the framing.
        drawing_setup = '''
            const canvas = document.getElementById('doodle-canvas');
            const imgContainer = document.getElementById('image-container');
            const doodleActions = window.actions;
            const renderSegment = annotations.renderSegment;
            const narrationPane = { setDrawingColorSelected() {} };
            let textAnnotationMode = false, lastSelectedTool = { type: 'marker', color: '#ff0000' };
            let activeTextEditor = null;
            const isPagedBack = false, pagingControls = null;
            const textAnnotationToggle = document.getElementById('text-annotation-toggle');
            const colorBtns = document.querySelectorAll('.color-btn'), doodleRenderer = annotations;
            function setTextAnnotationMenuOpen() {}
            function setPaletteOpen() {}
            function canAddTextAnnotation() { return true; }
            let doodlesEnabled = true, currentColor = '#ff0000', currentBrushSize = 6;
            let isDrawing = false, lastNormX = 0, lastNormY = 0, currentStrokeId = null;
            let pendingDrawBatch = null, drawBatchFrame = null;
            window.sentDraws = [];
            function sendOrQueueDoodleMessage(message) { sentDraws.push(message); }
        '''
        draw_functions = template[template.index("        function drawLine("):template.index("        function beginTextAnnotation(")]
        flush_function = template[template.index("        function flushDrawBatch("):template.index("        function isActiveCanvasWindow(")]
        tool_interaction = template[template.index("        function refreshCanvasToolInteraction("):template.index("        function setTextAnnotationMode(")]
        marker_selection = template[template.index("        function setDrawingColor("):template.index("        function cycleCanvasCursor(")]
        text_selection = template[template.index("        function setTextAnnotationMode("):template.index("        function updateTextAnnotationAvailability(")]
        marker_controls = template[template.index("        colorBtns.forEach(btn => {"):template.index("        canvasCursorState.subscribe(applyCanvasCursorState);")]
        text_control = template[template.index("        textAnnotationToggle?.addEventListener('click',"):template.index("        textAnnotationSize?.addEventListener('input',")]
        cycle = template[template.index("        function cycleCanvasCursor("):template.index("        document.addEventListener('click', (event) => {", template.index("        function cycleCanvasCursor("))]
        context_menu = template[template.index("        document.addEventListener('contextmenu', (event) => {"):template.index("        textAnnotationToggle?.addEventListener('click',")]
        handlers = template[template.index("        canvas.addEventListener('mousedown',"):template.index("        clearBtn.addEventListener('click'")]
        page.add_script_tag(content=drawing_setup + flush_function + draw_functions + handlers + tool_interaction + marker_selection + text_selection + marker_controls + text_control)
        page.evaluate("canvasCursorState.subscribe(applyCanvasCursorState); applyCanvasCursorState(canvasCursorState.current)")
        states = [
            {"type": "select"}, {"type": "marker", "color": "#ff0000"},
            {"type": "marker", "color": "#00ff00"}, {"type": "text"}, {"type": "navigate"},
        ]
        # Exercise every transition through the real control handlers, including toggling off.
        for source in states:
            for target in states:
                page.evaluate("state => canvasCursorState.select(state)", source)
                if target["type"] == "marker":
                    page.locator(f'.color-btn[data-color="{target["color"]}"]').click()
                elif target["type"] == "text":
                    page.get_by_role('button', name='Text tool').click()
                elif target["type"] == "navigate":
                    button.click()
                else:
                    page.evaluate("setDrawingColor(null)")
                expected = {"type": "select"} if source == target else target
                assert page.evaluate("canvasCursorState.current.type") == expected["type"]
                assert page.locator('.color-btn[aria-pressed="true"]').count() == (1 if expected["type"] == "marker" else 0)
                expect(button).to_have_attribute('aria-pressed', str(expected["type"] == "navigate").lower())
                expect(page.get_by_role('button', name='Text tool')).to_have_attribute('aria-pressed', str(expected["type"] == "text").lower())
                assert page.evaluate("canvas.style.pointerEvents") == ('auto' if expected["type"] in ('marker', 'text') else 'none')
                assert page.evaluate("currentColor") == expected.get("color")
                assert stamp.bounding_box()["x"] == pytest.approx(moved["x"], abs=0.1)
        page.evaluate("canvasCursorState.select({type: 'navigate'})")
        # Viewer right-click and the real orator wheel center run the same three-state cycle.
        wheel_markup = template[template.index('    <style>\n        #orator-action-wheel'):template.index('    <div id="action-wheel-status"')]
        page.locator('body').evaluate("(body, markup) => body.insertAdjacentHTML('beforeend', markup)", wheel_markup + '''
            <button id="action-wheel-rebind"><span id="action-wheel-binding-label"></span></button>
            <div id="action-wheel-status" hidden></div>
        ''')
        page.evaluate('''async () => {
            window.initializeActionWheel = (await import('https://canvas.test/static/js/action-wheel.js')).initializeActionWheel;
        }''')
        center_action = template[template.index("            onCenterAction: () => {"):template.index("            onRebindStart: () => {")]
        page.add_script_tag(content='''
            let orator = false;
            function isCurrentOrator() { return orator; }
            const actionWheel = initializeActionWheel({isOrator: isCurrentOrator, canvasTarget: imgContainer,
                sendAction: async () => {},
        ''' + center_action + '});\n' + cycle + context_menu)
        for orator in [False, True]:
            page.evaluate("value => { orator = value; }", orator)
            for tool in [{"type": "marker", "color": "#00ff00"}, {"type": "marker", "color": "erase"}, {"type": "text"}]:
                page.evaluate("tool => canvasCursorState.select(tool)", tool)
                for expected_type in ['select', 'navigate', tool['type']]:
                    page.mouse.move(208, 158)
                    page.mouse.down(button='right')
                    if orator:
                        expect(page.locator('#orator-action-wheel')).to_be_visible()
                        expect(page.locator('.wheel-center')).to_have_text('Cyclecursor')
                    page.mouse.up(button='right')
                    assert page.evaluate("canvasCursorState.current.type") == expected_type
                    if expected_type == 'marker':
                        assert page.evaluate("currentColor") == tool['color']
                    assert stamp.bounding_box()["x"] == pytest.approx(moved["x"], abs=0.1)
        page.evaluate("orator = false; canvasCursorState.select({type: 'navigate'})")
        # Routine refreshes with an existing marker must not cancel navigation.
        page.evaluate("refreshCanvasToolInteraction()")
        expect(button).to_have_attribute("aria-pressed", "true")
        expect(surface).to_be_visible()
        page.evaluate("setDrawingColor('#ff0000')")
        expect(button).to_have_attribute("aria-pressed", "false")
        expect(page.get_by_role('button', name='Red marker')).to_have_attribute('aria-pressed', 'true')
        button.click()
        page.evaluate("refreshCanvasToolInteraction()")
        expect(button).to_have_attribute("aria-pressed", "true")
        expect(page.get_by_role('button', name='Red marker')).to_have_attribute('aria-pressed', 'false')
        assert page.evaluate("currentColor") is None
        # Choosing a text tool exits navigation, but subsequent refreshes do not.
        page.evaluate("setTextAnnotationMode(true)")
        expect(button).to_have_attribute("aria-pressed", "false")
        expect(page.get_by_role('button', name='Text tool')).to_have_attribute('aria-pressed', 'true')
        button.click()
        page.evaluate("refreshCanvasToolInteraction()")
        expect(button).to_have_attribute("aria-pressed", "true")
        expect(page.get_by_role('button', name='Text tool')).to_have_attribute('aria-pressed', 'false')
        assert not page.evaluate("textAnnotationMode")
        page.evaluate("setDrawingColor('#00ff00')")
        expect(page.get_by_role('button', name='Green marker')).to_have_attribute('aria-pressed', 'true')
        expect(page.get_by_role('button', name='Red marker')).to_have_attribute('aria-pressed', 'false')
        page.evaluate("setDrawingColor('#ff0000')")
        expect(page.get_by_role('button', name='Green marker')).to_have_attribute('aria-pressed', 'false')
        expect(surface).to_be_hidden()
        assert stamp.bounding_box()["x"] == pytest.approx(moved["x"], abs=0.1)
        drawing_rect = page.locator("#doodle-canvas").bounding_box()
        page.mouse.move(208, 158)
        page.mouse.down()
        page.mouse.move(228, 178)
        page.mouse.up()
        stroke = page.evaluate("actions.at(-1)")
        assert stroke["type"] == "draw"
        assert stroke["x0"] == pytest.approx((208 - drawing_rect["x"]) / drawing_rect["width"])
        assert stroke["y0"] == pytest.approx((158 - drawing_rect["y"]) / drawing_rect["height"])
        assert stroke["x1"] == pytest.approx((228 - drawing_rect["x"]) / drawing_rect["width"])
        assert stroke["y1"] == pytest.approx((178 - drawing_rect["y"]) / drawing_rect["height"])
        page.wait_for_function("sentDraws.length > 0")
        assert page.evaluate("sentDraws.at(-1).points") == pytest.approx([stroke["x0"], stroke["y0"], stroke["x1"], stroke["y1"]])
        assert page.evaluate('''() => {
            const ctx = canvas.getContext('2d');
            const stroke = actions.at(-1);
            return Array.from(ctx.getImageData((stroke.x0 + stroke.x1) / 2 * canvas.clientWidth,
                (stroke.y0 + stroke.y1) / 2 * canvas.clientHeight, 1, 1).data);
        }''') == [255, 0, 0, 255]
        # A tool transition finishes a stroke even if the mouse is still held down.
        page.mouse.move(208, 158)
        page.mouse.down()
        page.mouse.move(218, 168)
        page.evaluate("canvasCursorState.select({type: 'navigate'})")
        assert not page.evaluate("isDrawing")
        assert page.evaluate("pendingDrawBatch") is None
        stroke_count = page.evaluate("actions.length")
        page.mouse.move(238, 188)
        page.mouse.up()
        assert page.evaluate("actions.length") == stroke_count
        # Stamp dragging also converts the retained view back to theater coordinates.
        page.evaluate("setDrawingColor(null)")
        stamp_center_x = moved["x"] + moved["width"] / 2
        stamp_center_y = moved["y"] + moved["height"] / 2
        page.mouse.move(stamp_center_x, stamp_center_y)
        page.mouse.down()
        page.mouse.move(stamp_center_x + 20, stamp_center_y + 10)
        page.mouse.up()
        assert page.evaluate("actions[1].x") == pytest.approx(0.5 + 20 / drawing_rect["width"])
        assert page.evaluate("actions[1].y") == pytest.approx(0.5 + 10 / drawing_rect["height"])
        button.click()
        assert stamp.bounding_box()["width"] == pytest.approx(moved["width"], abs=0.1)
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
        assert stamp.bounding_box()["width"] == pytest.approx(original_stamp["width"] * 0.5, abs=0.1)
        assert page.locator("#doodle-canvas").bounding_box()["width"] == 300
        # Reset also works while navigation is off.
        page.locator("#image-container").dblclick(position={"x": 100, "y": 100})
        assert stamp.bounding_box()["width"] == pytest.approx(original_stamp["width"], abs=0.1)
        assert page.locator("#doodle-canvas").bounding_box()["width"] == 600
        browser.close()
