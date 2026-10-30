"""Browser coverage for page-specific doodle replay and live message isolation."""

from collections.abc import Iterator
from pathlib import Path
import sys

from playwright.sync_api import Page, expect, sync_playwright
import pytest


@pytest.fixture
def doodle_history_page() -> Iterator[Page]:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    renderer = Path("static/js/canvas-renderers.js").read_text(encoding="utf-8")
    renderer = renderer[renderer.index("export function createDoodleRenderer("):]
    history = template[template.index("        function renderDoodleBatch(data) {"):
                       template.index("        function flushDrawBatch() {")]
    toggle = template[template.index("        function setDoodlesDisplay(enabled) {"):
                      template.index("        if (brushSizeSlider) {")]
    interaction = template[template.index("        function refreshCanvasToolInteraction() {"):
                           template.index("        function applyCanvasCursorState(state) {")]
    send = template[template.index("        function sendOrQueueDoodleMessage(message) {"):
                    template.index("        function flushQueuedDoodleMessages() {")]
    flush = template[template.index("        function flushQueuedDoodleMessages() {"):
                     template.index("        function scheduleDoodleReconnect(")]
    socket = template[template.index("            socket.onmessage = (event) => {"):
                      template.index("        document.addEventListener('visibilitychange'", template.index("            socket.onmessage = (event) => {"))]
    socket = socket[:socket.rindex("        }")]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 800, "height": 600})
        page.set_content('<button id="doodles-toggle"></button><div id="toolbar"></div>'
                         '<div style="position:relative;width:800px;height:500px">'
                         '<canvas id="doodle-canvas" style="width:800px;height:500px"></canvas>'
                         '<div id="canvas-text-layer"></div><div id="canvas-stamp-layer"></div></div>')
        page.add_script_tag(content=renderer.replace("export function", "function"))
        page.add_script_tag(content="""
            const canvas = document.getElementById('doodle-canvas');
            const doodleToolbar = document.getElementById('toolbar');
            const doodlesToggleBtn = document.getElementById('doodles-toggle');
            let displayedDoodlePageId = null;
            let doodlePageRequestSequence = 0;
            let doodleActions = [];
            let doodlesEnabled = true;
            let isDrawing = false;
            let currentColor = null;
            let textAnnotationMode = false;
            let activeTextEditor = null;
            let isPagedBack = false;
            let nextDoodleMessageId = 0;
            let doodleSocketReady = true;
            const pendingDoodleMessages = [];
            const inFlightDoodleMessages = new Map();
            const pendingTextEdits = new Map();
            const sent = [];
            const subscriptions = [];
            const ws = {readyState: WebSocket.OPEN, send: data => {
                const message = JSON.parse(data);
                if (message.type === 'subscribe_doodles') {
                    subscriptions.push(message.page_id);
                    const reply = () => socket.onmessage({data: JSON.stringify(snapshots[message.page_id])});
                    if (window.holdSnapshots) window.subscriptionResponses[message.page_id] = reply;
                    else reply();
                } else {
                    if (message.type === 'toggle_doodles') snapshots[message.page_id].enabled = message.enabled;
                    sent.push(message);
                }
            }};
            const socket = {};
            let refreshCount = 0;
            const fetchLatestImage = () => { refreshCount++; };
            const flushDrawBatch = () => {};
            const scheduleDoodleReconnect = () => {};
            const isCurrentOrator = () => true;
            const updateTextAnnotationAvailability = () => {};
            const narrationPane = {setDrawingColorSelected: () => {}};
            const doodleRenderer = createDoodleRenderer({canvas,
                textLayer: document.getElementById('canvas-text-layer'),
                stampLayer: document.getElementById('canvas-stamp-layer')});
            const redrawAllDoodles = () => doodleRenderer.redraw(doodleActions);
            const renderTextAnnotation = doodleRenderer.renderText;
            const renderSegment = doodleRenderer.renderSegment;
            const snapshots = {
                first: {type:'doodle_snapshot', page_id:'first', enabled:true,
                    batches:[{points:[0.1,0.1,0.2,0.2],color:'#ef4444',size:3}],
                    annotations:[{type:'text', id:'clue', text:'First clue', x:0.2, y:0.3, color:'#ffffff', size:32}]},
                second: {type:'doodle_snapshot', page_id:'second', enabled:true, batches:[], annotations:[]}
            };
            let imageHistory = [{page_id:'first'}, {page_id:'second'}];
            doodleRenderer.resize([]);
        """ + history + toggle + interaction + send + flush + socket)
        yield page
        browser.close()


def test_page_doodles_and_visibility_restore_when_browsing_back(doodle_history_page: Page) -> None:
    page = doodle_history_page
    page.evaluate("displayPageDoodles(imageHistory[0])")
    expect(page.locator('.canvas-text-annotation')).to_have_text('First clue')
    first_drawing = page.locator('canvas').evaluate('canvas => canvas.toDataURL()')
    page.locator('#doodles-toggle').click()
    assert page.evaluate('sent[0]') == {
        'type': 'toggle_doodles', 'enabled': False, 'page_id': 'first',
        'client_message_id': page.evaluate('sent[0].client_message_id'),
    }
    expect(page.locator('#doodle-canvas')).to_be_hidden()
    page.evaluate("displayPageDoodles(imageHistory[1])")
    expect(page.locator('#doodles-toggle')).to_have_text('✏️ Doodles: On')
    expect(page.locator('.canvas-text-annotation')).to_have_count(0)
    assert page.locator('canvas').evaluate('canvas => canvas.toDataURL()') != first_drawing
    page.evaluate("isPagedBack = true; displayPageDoodles(imageHistory[0])")
    expect(page.locator('#doodles-toggle')).to_have_text('✏️ Doodles: Off')
    page.locator('#doodles-toggle').click()
    expect(page.locator('#canvas-text-layer')).to_be_visible()
    expect(page.locator('.canvas-text-annotation')).to_have_text('First clue')
    assert page.locator('canvas').evaluate('canvas => canvas.toDataURL()') == first_drawing
    assert page.locator('canvas').get_attribute('style').find('pointer-events: none') >= 0
    assert page.evaluate('sent[1].enabled') is True
    assert page.evaluate('sent[1].page_id') == 'first'
    assert page.evaluate('subscriptions') == ['first', 'second', 'first']
    assert page.evaluate('imageHistory.every(page => page.doodles === undefined)')


def test_live_messages_for_another_page_do_not_replace_browsed_doodles(doodle_history_page: Page) -> None:
    page = doodle_history_page
    page.evaluate("""() => {
        displayPageDoodles(imageHistory[0]);
        isPagedBack = true;
        for (const type of ['clear', 'draw_batch', 'doodles_toggle', 'doodle_snapshot']) {
            socket.onmessage({data: JSON.stringify({type, page_id:'second', enabled:false, batches:[]})});
        }
    }""")
    expect(page.locator('.canvas-text-annotation')).to_have_text('First clue')
    expect(page.locator('#doodles-toggle')).to_have_text('✏️ Doodles: On')
    assert page.evaluate('doodleActions.length') == 2
    assert page.evaluate('refreshCount') == 0
    page.evaluate("socket.onmessage({data: JSON.stringify({type:'clear', page_id:'first'})})")
    expect(page.locator('.canvas-text-annotation')).to_have_count(0)
    assert page.evaluate('doodleActions.length') == 0


def test_page_snapshots_load_on_demand_and_stale_responses_are_ignored(doodle_history_page: Page) -> None:
    page = doodle_history_page
    page.evaluate("""() => {
        ws.readyState = WebSocket.CLOSED;
        window.currenttheaterId = 'stage';
        window.snapshotRequests = [];
        window.snapshotResponses = {};
        window.fetch = url => new Promise(resolve => {
            const pageId = new URL(url, 'https://canvas.test').searchParams.get('page_id');
            snapshotRequests.push(pageId);
            snapshotResponses[pageId] = () => resolve({ok:true, json:async () => snapshots[pageId]});
        });
        displayPageDoodles(imageHistory[0]);
        displayPageDoodles(imageHistory[1]);
        snapshotResponses.first();
    }""")
    expect(page.locator('.canvas-text-annotation')).to_have_count(0)
    assert page.evaluate('displayedDoodlePageId') == 'second'
    page.evaluate("snapshotResponses.second()")
    page.evaluate("displayPageDoodles(imageHistory[1])")
    assert page.evaluate('snapshotRequests') == ['first', 'second']
    assert page.evaluate('doodleActions.length') == 0
    # A socket can open while the HTTP fallback is still in flight. Its newer
    # snapshot and live clear must win over that older HTTP response.
    page.evaluate("""async () => {
        displayPageDoodles(imageHistory[0]);
        socket.onmessage({data: JSON.stringify(snapshots.first)});
        socket.onmessage({data: JSON.stringify({type:'clear', page_id:'first'})});
        snapshotResponses.first();
        await new Promise(resolve => setTimeout(resolve, 0));
    }""")
    expect(page.locator('.canvas-text-annotation')).to_have_count(0)
    assert page.evaluate('doodleActions.length') == 0



def test_drawing_while_page_snapshot_loads_is_replayed(doodle_history_page: Page) -> None:
    page = doodle_history_page
    page.evaluate("""() => {
        window.holdSnapshots = true;
        window.subscriptionResponses = {};
        displayPageDoodles(imageHistory[0]);
        const batch = {type:'draw_batch', points:[0.7,0.2,0.8,0.2], color:'#00ff00', size:5};
        renderDoodleBatch(batch);
        sendOrQueueDoodleMessage(batch);
        subscriptionResponses.first();
    }""")
    assert page.evaluate('pendingDoodleMessages.length') == 0
    assert page.evaluate('sent[0].type') == 'draw_batch'
    assert page.evaluate('doodleActions.length') == 3
    assert page.locator('canvas').evaluate("canvas => Array.from(canvas.getContext('2d').getImageData(600,100,1,1).data)") == [0, 255, 0, 255]
