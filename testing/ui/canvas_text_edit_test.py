"""Browser coverage for selecting and editing persisted canvas annotations."""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright


@pytest.fixture
def text_page() -> Iterator[Page]:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    styles = template[template.index("        #canvas-text-layer {"):
                      template.index("        .eraser-btn {", template.index("        #canvas-text-layer {"))]
    editor = template[template.index("        function applyTextAnnotation(action) {"):
                      template.index("        canvas.addEventListener('click', beginTextAnnotation);")]
    renderer = Path("static/js/canvas-renderers.js").read_text(encoding="utf-8")
    renderer = renderer[renderer.index("export function createDoodleRenderer("):]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 800, "height": 600})
        page.set_content(
            f"<style>{styles}</style>"
            '<div id="image-container" style="position:relative;width:800px;height:500px">'
            '<canvas id="doodle-canvas" style="width:100%;height:100%"></canvas>'
            '<div id="canvas-text-layer"></div></div>'
        )
        page.add_script_tag(content=renderer.replace("export function", "function"))
        page.add_script_tag(content="""
            const canvas = document.getElementById('doodle-canvas');
            const imgContainer = document.getElementById('image-container');
            let activeTextEditor = null;
            let isPagedBack = false;
            let editable = true;
            const canAddTextAnnotation = () => editable;
            const pendingTextEdits = new Map();
            const currentTextColor = '#ffffff';
            const textAnnotationFont = {value: 'Outfit'};
            const textAnnotationSize = {value: '32'};
            const sent = [];
            function sendOrQueueDoodleMessage(action) {
                if (!action.client_message_id) action.client_message_id = 'message-' + sent.length;
                sent.push(action);
            }
            let doodleActions = [{type:'text', id:'clue', x:0.1, y:0.1,
                text:'Original clue', color:'#ffffff', size:32, font:'Outfit'}];
            const renderer = createDoodleRenderer({canvas,
                textLayer: document.getElementById('canvas-text-layer'),
                canEditText: () => editable && !isPagedBack,
                onEditText: action => openTextAnnotationEditor(action),
                onMoveText: (action, previous, commit) => moveTextAnnotation(action, previous, commit)});
            const redrawAllDoodles = () => renderer.redraw(doodleActions);
        """ + editor + "\nrenderer.resize(doodleActions);")
        yield page
        browser.close()


def test_select_edit_cancel_and_replay_text(text_page: Page) -> None:
    label = text_page.locator('.canvas-text-annotation')
    assert label.inner_text() == 'Original clue'
    label.evaluate("el => { const range = document.createRange(); range.selectNodeContents(el); "
                   "const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range); }")
    assert text_page.evaluate("window.getSelection().toString()") == 'Original clue'
    before = text_page.locator('canvas').evaluate("el => el.toDataURL()")
    label.dblclick()
    editor = text_page.get_by_label('Text annotation', exact=True)
    assert editor.input_value() == 'Original clue'
    editor.fill('Updated clue')
    editor.press('Enter')
    assert label.inner_text() == 'Updated clue'
    assert text_page.evaluate('sent[0].id') == 'clue'
    assert text_page.evaluate('doodleActions.length') == 1
    assert text_page.locator('canvas').evaluate("el => el.toDataURL()") != before
    label.focus()
    label.press('Enter')
    editor.fill('Cancelled')
    editor.press('Escape')
    assert label.inner_text() == 'Updated clue'
    text_page.evaluate('renderer.resize(doodleActions)')
    assert label.count() == 1
    text_page.evaluate('editable = false')
    label.dblclick()
    assert editor.count() == 0
    text_page.evaluate('doodleActions = []; redrawAllDoodles()')
    assert label.count() == 0


def test_click_selects_then_drag_moves_and_double_click_edits(text_page: Page) -> None:
    label = text_page.locator('.canvas-text-annotation')
    label.click()
    assert label.evaluate("el => el.classList.contains('movable')")
    assert text_page.get_by_label('Text annotation', exact=True).count() == 0
    bounds = label.bounding_box()
    assert bounds is not None
    x, y = bounds['x'] + 15, bounds['y'] + 15
    before = text_page.locator('canvas').evaluate("el => el.toDataURL()")
    text_page.mouse.move(x, y)
    text_page.mouse.down()
    text_page.mouse.move(x + 160, y + 100, steps=5)
    assert text_page.evaluate('sent.length') == 0
    text_page.mouse.up()
    assert text_page.evaluate('sent.length') == 1
    assert text_page.evaluate('sent[0].x') == pytest.approx(0.3)
    assert text_page.evaluate('sent[0].y') == pytest.approx(0.3)
    assert text_page.evaluate('sent[0].id') == 'clue'
    assert text_page.evaluate('doodleActions.length') == 1
    assert text_page.locator('canvas').evaluate("el => el.toDataURL()") != before
    text_page.evaluate('renderer.resize(doodleActions)')
    assert label.evaluate('el => el.style.left') == '30%'
    label.dblclick()
    editor = text_page.get_by_label('Text annotation', exact=True)
    assert editor.input_value() == 'Original clue'
    editor.fill('Moved clue')
    editor.press('Enter')
    assert text_page.evaluate('sent[1].x') == pytest.approx(0.3)
    assert text_page.evaluate('sent[1].y') == pytest.approx(0.3)
    assert label.inner_text() == 'Moved clue'
    label.click()
    bounds = label.bounding_box()
    assert bounds is not None
    text_page.mouse.move(bounds['x'] + 15, bounds['y'] + 15)
    text_page.mouse.down()
    text_page.mouse.move(bounds['x'] + 95, bounds['y'] + 65)
    text_page.mouse.up()
    assert text_page.evaluate('sent[2].x') == pytest.approx(0.4)
    assert text_page.evaluate('sent[2].client_message_id') != text_page.evaluate('sent[1].client_message_id')
    text_page.mouse.click(700, 450)
    assert not label.evaluate("el => el.classList.contains('movable')")


def test_drag_cancel_and_permissions(text_page: Page) -> None:
    label = text_page.locator('.canvas-text-annotation')
    label.click()
    bounds = label.bounding_box()
    assert bounds is not None
    text_page.mouse.move(bounds['x'] + 15, bounds['y'] + 15)
    text_page.mouse.down()
    text_page.mouse.move(bounds['x'] + 175, bounds['y'] + 115)
    label.dispatch_event('pointercancel', {'pointerId': 1})
    text_page.mouse.up()
    assert text_page.evaluate('doodleActions[0].x') == pytest.approx(0.1)
    assert text_page.evaluate('sent.length') == 0
    text_page.evaluate('editable = false')
    label.click()
    text_page.mouse.move(bounds['x'] + 15, bounds['y'] + 15)
    text_page.mouse.down()
    text_page.mouse.move(bounds['x'] + 175, bounds['y'] + 115)
    text_page.mouse.up()
    assert text_page.evaluate('doodleActions[0].x') == pytest.approx(0.1)
    assert text_page.evaluate('sent.length') == 0
