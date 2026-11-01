"""Browser and state tests for canvas doodle undo procedures."""

from collections.abc import Iterator
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.sync_api import Page, sync_playwright

from components.canvas.doodle_state import DoodleState


@pytest.fixture
def undo_page() -> Iterator[Page]:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    renderer = Path("static/js/canvas-renderers.js").read_text(encoding="utf-8")
    renderer = renderer[renderer.index("export function createDoodleRenderer("):]
    doodle_actions_module = Path("static/js/canvas-doodle-actions.js").read_text(encoding="utf-8")

    start_undo = template.index("        function updateUndoButtonState() {")
    end_undo = template.index("        function renderDoodleBatch(data) {")
    undo_code = template[start_undo:end_undo]

    start_draw = template.index("        function drawLine(nx0, ny0, nx1, ny1, color, size, emit) {")
    end_draw = template.index("        function getNormalizedCoords(e) {")
    draw_code = template[start_draw:end_draw]

    start_text_stamp = template.index("        function applyTextAnnotation(action) {")
    end_text_stamp = template.index("        canvas.addEventListener('click', beginTextAnnotation);")
    text_stamp_code = template[start_text_stamp:end_text_stamp]

    start_end_drawing = template.index("        function endDrawingStroke() {")
    end_end_drawing = template.index("        canvas.addEventListener('mousedown', (e) => {")
    end_drawing_code = template[start_end_drawing:end_end_drawing]

    start_place_stamp = template.index("        function placeStampOnCanvas(stampData, normX, normY) {")
    end_place_stamp = template.index("        if (imgContainer) {", start_place_stamp)
    place_stamp_code = template[start_place_stamp:end_place_stamp]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 800, "height": 600})
        page.set_content(
            '<div id="image-container" style="position:relative;width:800px;height:500px">'
            '<canvas id="doodle-canvas" style="width:100%;height:100%"></canvas>'
            '<div id="canvas-text-layer"></div>'
            '<div id="canvas-stamp-layer"></div>'
            '</div>'
            '<div id="doodle-toolbar">'
            '<button id="undo-doodle-btn" type="button" disabled>Undo</button>'
            '<button id="clear-doodle-btn" type="button">Clear</button>'
            '</div>'
        )
        page.add_script_tag(content=renderer.replace("export function", "function"))
        page.add_script_tag(content=doodle_actions_module.replace("export function", "function").replace("export class", "class").replace("export const", "const"))
        page.add_script_tag(content="""
            const canvas = document.getElementById('doodle-canvas');
            const imgContainer = document.getElementById('image-container');
            const undoBtn = document.getElementById('undo-doodle-btn');
            const clearBtn = document.getElementById('clear-doodle-btn');
            const textLayer = document.getElementById('canvas-text-layer');
            const stampLayer = document.getElementById('canvas-stamp-layer');

            let doodleActions = [];
            let doodleUndoStack = [];
            let activeDeleteTransaction = null;
            let doodlesEnabled = true;
            let isPagedBack = false;
            let displayedDoodlePageId = 'page-1';
            let currentStrokeId = null;
            let isDrawing = false;
            let currentColor = '#ef4444';
            let currentBrushSize = 3;
            let currentTextColor = '#ffffff';
            let textAnnotationMode = false;
            let activeTextEditor = null;
            let pendingDrawBatch = null;
            let drawBatchFrame = null;
            const pendingDoodleMessages = [];
            const inFlightDoodleMessages = new Map();
            const pendingTextEdits = new Map();
            const sentMessages = [];
            const textAnnotationFont = { value: 'Outfit' };
            const textAnnotationSize = { value: '32' };
            const myUserId = 'user-test';

            const canvasCursorState = { select: () => {} };
            const canAddTextAnnotation = () => true;
            const canPlaceStamps = () => true;
            const recordStampUsage = () => {};
            const flushDrawBatch = () => {
                if (pendingDrawBatch) {
                    sendOrQueueDoodleMessage(pendingDrawBatch);
                    pendingDrawBatch = null;
                }
            };

            function sendOrQueueDoodleMessage(message) {
                sentMessages.push({ ...message });
            }

            const doodleRenderer = createDoodleRenderer({
                canvas,
                isVisible: () => doodlesEnabled,
                textLayer,
                canEditText: () => canAddTextAnnotation() && !isPagedBack,
                onEditText: action => openTextAnnotationEditor(action),
                onMoveText: (action, previous, commit) => moveTextAnnotation(action, previous, commit),
                stampLayer,
                canMoveStamp: () => canPlaceStamps() && !isPagedBack,
                onMoveStamp: (action, previous, commit) => moveStampAnnotation(action, previous, commit),
                onRemoveStamp: action => removeStampAnnotation(action),
                onSelectStamp: () => {},
                onRemoveText: action => removeTextAnnotation(action),
                canSelectSegment: () => doodlesEnabled && !isPagedBack,
                onRemoveSegment: (removed) => {
                    const removedSet = new Set((removed || []).map(r => r.id || r));
                    const removedSegments = doodleActions.filter(a => removedSet.has(a.id) || removedSet.has(a));
                    doodleActions = doodleActions.filter(a => !removedSet.has(a.id) && !removedSet.has(a));
                    if (removedSegments.length > 0) {
                        if (activeDeleteTransaction) {
                            activeDeleteTransaction.segments.push(...removedSegments);
                        } else {
                            pushUndoAction({
                                type: 'delete_segments',
                                segments: removedSegments
                            });
                        }
                        sendOrQueueDoodleMessage({
                            type: 'remove_draw',
                            ids: removedSegments.map(s => s.id).filter(Boolean)
                        });
                    }
                    redrawAllDoodles();
                },
            });
            const redrawAllDoodles = () => doodleRenderer.redraw(doodleActions);
            const renderSegment = doodleRenderer.renderSegment;

            undoBtn.addEventListener('click', () => {
                if (!doodlesEnabled || isPagedBack) return;
                undoLastDoodleAction();
            });

            document.addEventListener('keydown', (event) => {
                if (event.key === 'Delete' || event.key === 'Backspace') {
                    const active = document.activeElement;
                    if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) return;
                    activeDeleteTransaction = {
                        type: 'compound_delete',
                        segments: [],
                        stamps: [],
                        texts: []
                    };
                    queueMicrotask(() => {
                        if (activeDeleteTransaction) {
                            const tx = activeDeleteTransaction;
                            activeDeleteTransaction = null;
                            if (tx.segments.length > 0 || tx.stamps.length > 0 || tx.texts.length > 0) {
                                pushUndoAction(tx);
                            }
                        }
                    });
                }
            }, true);

            window.addEventListener('keydown', (event) => {
                const isZ = event.key === 'z' || event.key === 'Z' || event.code === 'KeyZ';
                if ((event.ctrlKey || event.metaKey) && isZ && !event.shiftKey && !event.altKey) {
                    const active = document.activeElement;
                    if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable || (typeof active.getAttribute === 'function' && active.getAttribute('contenteditable') === 'true'))) return;
                    if (typeof doodlesEnabled !== 'undefined' && !doodlesEnabled) return;
                    if (typeof isPagedBack !== 'undefined' && isPagedBack) return;
                    event.preventDefault();
                    if (typeof undoLastDoodleAction === 'function') {
                        undoLastDoodleAction();
                    }
                }
            }, true);
        """ + undo_code + draw_code + text_stamp_code + end_drawing_code + place_stamp_code)
        yield page
        browser.close()


def test_undo_drawing_stroke_removes_segments_and_sends_remove_draw(undo_page: Page) -> None:
    page = undo_page
    page.evaluate("""() => {
        currentStrokeId = 'stroke-abc';
        isDrawing = true;
        drawLine(0.1, 0.1, 0.2, 0.2, '#ef4444', 3, true);
        drawLine(0.2, 0.2, 0.3, 0.3, '#ef4444', 3, true);
        endDrawingStroke();
    }""")
    count = page.evaluate("doodleActions.length")
    assert count == 2
    undo_disabled = page.evaluate("document.getElementById('undo-doodle-btn').disabled")
    assert undo_disabled is False

    # Perform undo
    page.evaluate("undoLastDoodleAction()")
    count_after = page.evaluate("doodleActions.length")
    assert count_after == 0
    undo_disabled_after = page.evaluate("document.getElementById('undo-doodle-btn').disabled")
    assert undo_disabled_after is True

    # Check WebSocket message sent
    last_msg = page.evaluate("sentMessages[sentMessages.length - 1]")
    assert last_msg["type"] == "remove_draw"
    assert last_msg["stroke_id"] == "stroke-abc"


def test_undo_drawing_segment_deletion_restores_segments(undo_page: Page) -> None:
    page = undo_page
    page.evaluate("""() => {
        doodleActions = [
            { type: 'draw', id: 'seg-1', stroke_id: 'str-1', x0: 0.1, y0: 0.1, x1: 0.2, y1: 0.2, color: '#ef4444', size: 3 },
            { type: 'draw', id: 'seg-2', stroke_id: 'str-2', x0: 0.2, y0: 0.2, x1: 0.3, y1: 0.3, color: '#ef4444', size: 3 }
        ];
        redrawAllDoodles();
        doodleRenderer.selectSegment('seg-1');
        // Delete selected
        const selected = doodleRenderer.getSelectedSegments();
        doodleRenderer.clearSelectedSegments();
        const removedSet = new Set(selected.map(r => r.id));
        const removed = doodleActions.filter(a => removedSet.has(a.id));
        doodleActions = doodleActions.filter(a => !removedSet.has(a.id));
        pushUndoAction({ type: 'delete_segments', segments: removed });
        redrawAllDoodles();
    }""")
    assert page.evaluate("doodleActions.length") == 1

    page.evaluate("undoLastDoodleAction()")
    assert page.evaluate("doodleActions.length") == 2
    last_msg = page.evaluate("sentMessages[sentMessages.length - 1]")
    assert last_msg["type"] == "draw_batch"
    assert last_msg["color"] == "#ef4444"


def test_restoring_disconnected_segments_does_not_send_a_fabricated_bridge(undo_page: Page) -> None:
    restored = undo_page.evaluate("""() => {
        const messages = [];
        restoreSegmentsToServer([
            {stroke_id:'one',x0:.1,y0:.1,x1:.2,y1:.2,color:'red',size:3},
            {stroke_id:'one',x0:.8,y0:.8,x1:.9,y1:.9,color:'red',size:3}
        ], message => messages.push(message));
        return messages;
    }""")
    assert len(restored) == 2
    assert [message["points"] for message in restored] == [[0.1, 0.1, 0.2, 0.2], [0.8, 0.8, 0.9, 0.9]]


def test_undo_text_placement_removes_text(undo_page: Page) -> None:
    page = undo_page
    page.evaluate("""() => {
        openTextAnnotationEditor(null, { x: 0.3, y: 0.4 });
        const editor = document.querySelector('.canvas-text-editor');
        editor.value = 'Secret Cave';
        editor.blur();
    }""")
    assert page.evaluate("doodleActions.length") == 1
    assert page.evaluate("doodleActions[0].text") == "Secret Cave"

    page.evaluate("undoLastDoodleAction()")
    assert page.evaluate("doodleActions.length") == 0
    last_msg = page.evaluate("sentMessages[sentMessages.length - 1]")
    assert last_msg["type"] == "remove_text"


def test_undo_text_edit_restores_previous_text(undo_page: Page) -> None:
    page = undo_page
    page.evaluate("""() => {
        const original = { id: 'text-1', type: 'text', x: 0.2, y: 0.2, text: 'Original Text', color: '#ffffff', size: 32, font: 'Outfit' };
        doodleActions.push(original);
        redrawAllDoodles();

        openTextAnnotationEditor(original);
        const editor = document.querySelector('.canvas-text-editor');
        editor.value = 'Updated Text';
        editor.blur();
    }""")
    assert page.evaluate("doodleActions[0].text") == "Updated Text"

    page.evaluate("undoLastDoodleAction()")
    assert page.evaluate("doodleActions[0].text") == "Original Text"
    last_msg = page.evaluate("sentMessages[sentMessages.length - 1]")
    assert last_msg["type"] == "text"
    assert last_msg["text"] == "Original Text"


def test_undo_text_move_restores_previous_position(undo_page: Page) -> None:
    page = undo_page
    page.evaluate("""() => {
        const textAction = { id: 'text-1', type: 'text', x: 0.2, y: 0.2, text: 'Movable', color: '#ffffff', size: 32, font: 'Outfit' };
        doodleActions.push(textAction);
        redrawAllDoodles();

        const previous = { ...textAction };
        const moved = { ...textAction, x: 0.6, y: 0.7 };
        moveTextAnnotation(moved, previous, true);
    }""")
    assert page.evaluate("doodleActions[0].x") == 0.6

    page.evaluate("undoLastDoodleAction()")
    assert page.evaluate("doodleActions[0].x") == 0.2
    assert page.evaluate("doodleActions[0].y") == 0.2


def test_undo_text_deletion_restores_text(undo_page: Page) -> None:
    page = undo_page
    page.evaluate("""() => {
        const textAction = { id: 'text-del', type: 'text', x: 0.2, y: 0.2, text: 'To Delete', color: '#ffffff', size: 32, font: 'Outfit' };
        doodleActions.push(textAction);
        redrawAllDoodles();

        removeTextAnnotation(textAction);
    }""")
    assert page.evaluate("doodleActions.length") == 0

    page.evaluate("undoLastDoodleAction()")
    assert page.evaluate("doodleActions.length") == 1
    assert page.evaluate("doodleActions[0].text") == "To Delete"


def test_undo_stamp_placement_removes_stamp(undo_page: Page) -> None:
    page = undo_page
    page.evaluate("""() => {
        placeStampOnCanvas({ stamp_id: 'dragon', url: '/stamps/dragon.png', name: 'Dragon' }, 0.5, 0.5);
    }""")
    assert page.evaluate("doodleActions.length") == 1
    assert page.evaluate("doodleActions[0].stamp_id") == "dragon"

    page.evaluate("undoLastDoodleAction()")
    assert page.evaluate("doodleActions.length") == 0
    last_msg = page.evaluate("sentMessages[sentMessages.length - 1]")
    assert last_msg["type"] == "remove_stamp"


def test_undo_stamp_replacement_restores_original_stamp(undo_page: Page) -> None:
    page = undo_page
    page.evaluate("""() => {
        placeStampOnCanvas({ stamp_id: 'flag', url: '/stamps/flag.png', name: 'Flag' }, 0.2, 0.2);
        placeStampOnCanvas({ stamp_id: 'flag', url: '/stamps/flag.png', name: 'Flag' }, 0.8, 0.8);
    }""")
    # Still only 1 flag on canvas
    assert page.evaluate("doodleActions.length") == 1
    assert page.evaluate("doodleActions[0].x") == 0.8

    # Undo second placement: should restore first stamp at x=0.2
    page.evaluate("undoLastDoodleAction()")
    assert page.evaluate("doodleActions.length") == 1
    assert page.evaluate("doodleActions[0].x") == 0.2


def test_undo_stamp_transformations_move_resize_rotate(undo_page: Page) -> None:
    page = undo_page
    page.evaluate("""() => {
        const stamp = { id: 'stamp-1', type: 'stamp', stamp_id: 'gem', user_id: 'user-test', url: '/gem.png', name: 'Gem', x: 0.3, y: 0.3, size: 80, rotation: 0 };
        doodleActions.push(stamp);
        redrawAllDoodles();

        // 1. Movement
        moveStampAnnotation({ ...stamp, x: 0.5, y: 0.5 }, { ...stamp }, true);
        // 2. Resize
        moveStampAnnotation({ ...stamp, x: 0.5, y: 0.5, size: 140 }, { ...stamp, x: 0.5, y: 0.5 }, true);
        // 3. Rotation
        moveStampAnnotation({ ...stamp, x: 0.5, y: 0.5, size: 140, rotation: 45 }, { ...stamp, x: 0.5, y: 0.5, size: 140 }, true);
    }""")
    assert page.evaluate("doodleActions[0].rotation") == 45

    # Undo rotation
    page.evaluate("undoLastDoodleAction()")
    assert page.evaluate("doodleActions[0].rotation") == 0
    assert page.evaluate("doodleActions[0].size") == 140

    # Undo resize
    page.evaluate("undoLastDoodleAction()")
    assert page.evaluate("doodleActions[0].size") == 80
    assert page.evaluate("doodleActions[0].x") == 0.5

    # Undo move
    page.evaluate("undoLastDoodleAction()")
    assert page.evaluate("doodleActions[0].x") == 0.3
    assert page.evaluate("doodleActions[0].y") == 0.3


def test_undo_stamp_deletion_restores_stamp(undo_page: Page) -> None:
    page = undo_page
    page.evaluate("""() => {
        const stamp = { id: 'stamp-del', type: 'stamp', stamp_id: 'star', user_id: 'user-test', url: '/star.png', name: 'Star', x: 0.4, y: 0.4, size: 80, rotation: 0 };
        doodleActions.push(stamp);
        redrawAllDoodles();

        removeStampAnnotation(stamp);
    }""")
    assert page.evaluate("doodleActions.length") == 0

    page.evaluate("undoLastDoodleAction()")
    assert page.evaluate("doodleActions.length") == 1
    assert page.evaluate("doodleActions[0].stamp_id") == "star"


def test_undo_compound_delete_restores_all_items(undo_page: Page) -> None:
    page = undo_page
    page.evaluate("""() => {
        const seg = { type: 'draw', id: 's1', stroke_id: 'str1', x0: 0.1, y0: 0.1, x1: 0.2, y1: 0.2, color: '#ef4444', size: 3 };
        const text = { type: 'text', id: 't1', x: 0.3, y: 0.3, text: 'Compound', color: '#ffffff', size: 32, font: 'Outfit' };
        const stamp = { type: 'stamp', id: 'm1', stamp_id: 'gem', user_id: 'user-test', url: '/gem.png', name: 'Gem', x: 0.4, y: 0.4, size: 80, rotation: 0 };
        doodleActions = [seg, text, stamp];
        redrawAllDoodles();

        // Simulate compound delete
        pushUndoAction({
            type: 'compound_delete',
            segments: [seg],
            stamps: [stamp],
            texts: [text]
        });
        doodleActions = [];
        redrawAllDoodles();
    }""")
    assert page.evaluate("doodleActions.length") == 0

    page.evaluate("undoLastDoodleAction()")
    assert page.evaluate("doodleActions.length") == 3
    types = page.evaluate("doodleActions.map(a => a.type)")
    assert "draw" in types
    assert "text" in types
    assert "stamp" in types


def test_undo_clear_canvas_restores_all_items(undo_page: Page) -> None:
    page = undo_page
    page.evaluate("""() => {
        const seg = { type: 'draw', id: 's1', stroke_id: 'str1', x0: 0.1, y0: 0.1, x1: 0.2, y1: 0.2, color: '#ef4444', size: 3 };
        const text = { type: 'text', id: 't1', x: 0.3, y: 0.3, text: 'Cleared Text', color: '#ffffff', size: 32, font: 'Outfit' };
        doodleActions = [seg, text];
        redrawAllDoodles();

        const previous = [...doodleActions];
        pushUndoAction({ type: 'clear', previousActions: previous });
        doodleActions = [];
        redrawAllDoodles();
    }""")
    assert page.evaluate("doodleActions.length") == 0

    page.evaluate("undoLastDoodleAction()")
    assert page.evaluate("doodleActions.length") == 2
    assert page.evaluate("doodleActions[1].text") == "Cleared Text"


def test_ctrl_z_keyboard_shortcut_triggers_undo(undo_page: Page) -> None:
    page = undo_page
    page.evaluate("""() => {
        currentStrokeId = 'stroke-ctrl-z-1';
        isDrawing = true;
        drawLine(0.1, 0.1, 0.2, 0.2, '#ef4444', 3, true);
        endDrawingStroke();

        currentStrokeId = 'stroke-ctrl-z-2';
        isDrawing = true;
        drawLine(0.3, 0.3, 0.4, 0.4, '#3b82f6', 3, true);
        endDrawingStroke();
    }""")
    assert page.evaluate("doodleActions.length") == 2

    # Add an input element to verify that typing inside an input ignores doodle Ctrl+Z
    page.evaluate("""() => {
        const inp = document.createElement('input');
        inp.id = 'test-typing-input';
        document.body.appendChild(inp);
        inp.focus();
    }""")
    page.keyboard.press("Control+z")
    # Should NOT have undone because an input was focused
    assert page.evaluate("doodleActions.length") == 2

    # Blur input, focus canvas
    page.evaluate("""() => {
        document.getElementById('test-typing-input').blur();
        document.getElementById('doodle-canvas').focus();
    }""")

    # Dispatch Ctrl+Z -> should undo the second stroke
    page.keyboard.press("Control+z")
    assert page.evaluate("doodleActions.length") == 1
    assert page.evaluate("doodleActions[0].stroke_id") == "stroke-ctrl-z-1"

    # Dispatch Meta+Z (Cmd+Z on macOS) -> should undo the first stroke
    page.keyboard.press("Meta+z")
    assert page.evaluate("doodleActions.length") == 0


def test_doodle_state_remove_draw_backend() -> None:
    persist_called = False

    def on_persist() -> None:
        nonlocal persist_called
        persist_called = True

    ds = DoodleState(persist=on_persist)
    ds.doodles = [
        {"type": "draw", "id": "s1", "stroke_id": "str1", "x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.2},
        {"type": "draw", "id": "s2", "stroke_id": "str1", "x0": 0.2, "y0": 0.2, "x1": 0.3, "y1": 0.3},
        {"type": "draw", "id": "s3", "stroke_id": "str2", "x0": 0.4, "y0": 0.4, "x1": 0.5, "y1": 0.5},
        {"type": "text", "id": "t1", "text": "Keep"},
    ]

    # Remove by stroke_id
    ds.remove_draw(stroke_id="str1")
    assert len(ds.doodles) == 2
    assert ds.doodles[0]["id"] == "s3"
    assert ds.doodles[1]["id"] == "t1"
    assert persist_called is True

    # Remove by id
    persist_called = False
    ds.remove_draw(ids=["s3"])
    assert len(ds.doodles) == 1
    assert ds.doodles[0]["id"] == "t1"
    assert persist_called is True


@pytest.mark.asyncio
async def test_api_server_remove_draw_websocket_message() -> None:
    from api_server import canvas

    state = MagicMock()
    state.connections = MagicMock()
    state.connections.processed_doodle_message_ids = set()
    state.connections.active_ws_connections = []
    state.doodles = MagicMock()
    state.doodles.page_id = ""

    sender = AsyncMock()
    sender.state = MagicMock()
    sender.state.theater_id = "test_stage"

    msg = {
        "type": "remove_draw",
        "stroke_id": "stroke-99",
        "ids": ["s1", "s2"],
        "client_message_id": "msg-123",
    }
    await canvas._apply_doodle_message(state, msg, sender)
    state.doodles.remove_draw.assert_called_once_with(stroke_id="stroke-99", ids=["s1", "s2"])
    sender.send_json.assert_awaited_once_with({"type": "doodle_ack", "client_message_id": "msg-123"})


def test_doodle_action_classes_and_sequence_tracking(undo_page: Page) -> None:
    page = undo_page
    # Verify all DoodleAction classes exist on window
    class_names = [
        "DoodleAction",
        "DrawStrokeDoodleAction",
        "DeleteSegmentsDoodleAction",
        "PlaceTextDoodleAction",
        "EditTextDoodleAction",
        "MoveTextDoodleAction",
        "DeleteTextDoodleAction",
        "PlaceStampDoodleAction",
        "TransformStampDoodleAction",
        "DeleteStampDoodleAction",
        "CompoundDeleteDoodleAction",
        "ClearCanvasDoodleAction",
    ]
    for cls in class_names:
        assert page.evaluate(f"typeof window.{cls} === 'function'")

    # Verify tracking sequence of instantiated DoodleAction subclasses
    page.evaluate("""() => {
        doodleActions = [];
        doodleUndoStack = [];

        // 1. Draw a stroke
        const seg = { type: 'draw', id: 'seg-1', stroke_id: 'stroke-1', x0: 0.1, y0: 0.1, x1: 0.2, y1: 0.2, color: '#ef4444', size: 3 };
        doodleActions.push(seg);
        redrawAllDoodles();
        pushUndoAction(new DrawStrokeDoodleAction('stroke-1', [seg]));

        // 2. Place text
        const textAction = { type: 'text', id: 'txt-1', x: 0.2, y: 0.2, text: 'First Text', color: '#ffffff', size: 32, font: 'Outfit' };
        doodleActions.push(textAction);
        redrawAllDoodles();
        pushUndoAction(new PlaceTextDoodleAction(textAction));

        // 3. Place stamp
        const stampAction = { type: 'stamp', id: 'st-1', stamp_id: 'apple', x: 0.5, y: 0.5, size: 48, rotation: 0 };
        doodleActions.push(stampAction);
        redrawAllDoodles();
        pushUndoAction(new PlaceStampDoodleAction(stampAction));
    }""")

    # Verify the sequence of 3 actions tracked
    assert page.evaluate("doodleUndoStack.length") == 3
    assert page.evaluate("doodleUndoStack[0] instanceof window.DrawStrokeDoodleAction") is True
    assert page.evaluate("doodleUndoStack[1] instanceof window.PlaceTextDoodleAction") is True
    assert page.evaluate("doodleUndoStack[2] instanceof window.PlaceStampDoodleAction") is True
    assert page.evaluate("doodleActions.length") == 3

    # Undo 1: should pop and undo PlaceStampDoodleAction
    page.evaluate("undoLastDoodleAction()")
    assert page.evaluate("doodleUndoStack.length") == 2
    assert page.evaluate("doodleActions.length") == 2
    assert page.evaluate("doodleActions.some(a => a.type === 'stamp')") is False

    # Undo 2: should pop and undo PlaceTextDoodleAction
    page.evaluate("undoLastDoodleAction()")
    assert page.evaluate("doodleUndoStack.length") == 1
    assert page.evaluate("doodleActions.length") == 1
    assert page.evaluate("doodleActions.some(a => a.type === 'text')") is False

    # Undo 3: should pop and undo DrawStrokeDoodleAction
    page.evaluate("undoLastDoodleAction()")
    assert page.evaluate("doodleUndoStack.length") == 0
    assert page.evaluate("doodleActions.length") == 0
