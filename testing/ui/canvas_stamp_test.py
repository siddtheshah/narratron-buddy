"""Tests for canvas stamp manager, contributor drag-and-drop, and stamp canvas persistence."""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright


def test_canvas_html_and_chat_css_stamp_wiring() -> None:
    canvas_html = Path("templates/canvas.html").read_text(encoding="utf-8")
    chat_css = Path("static/css/chat.css").read_text(encoding="utf-8")
    renderer_js = Path("static/js/canvas-renderers.js").read_text(encoding="utf-8")

    # Stamp button in chat composer
    assert 'id="chat-open-stamps-btn"' in canvas_html
    assert "#chat-open-stamps-btn" in chat_css

    # Stamp manager occupying chat pane
    assert 'id="stamp-manager-pane"' in canvas_html
    assert 'id="close-stamp-manager-btn"' in canvas_html
    assert 'id="stamp-manager-grid"' in canvas_html
    assert "#stamp-manager-pane" in chat_css
    assert ".stamp-grid" in chat_css
    assert ".stamp-card" in chat_css

    # Stamp manager recency ordering logic
    assert "recordStampUsage" in canvas_html
    assert "getStampLastUsedTimestamp" in canvas_html

    # Canvas stamp layer and annotations
    assert 'id="canvas-stamp-layer"' in canvas_html
    assert "#canvas-stamp-layer" in chat_css
    assert ".canvas-stamp-annotation" in chat_css
    assert ".stamp-resize-handle" in chat_css

    # Renderer support
    assert "stampLayer" in renderer_js
    assert "canMoveStamp" in renderer_js
    assert "onMoveStamp" in renderer_js
    assert "onSelectStamp" in renderer_js
    assert "renderSelectableStamp" in renderer_js
    assert "stamp-resize-handle" in renderer_js


@pytest.fixture
def stamp_page() -> Iterator[Page]:
    chat_css = Path("static/css/chat.css").read_text(encoding="utf-8")
    renderer_js = Path("static/js/canvas-renderers.js").read_text(encoding="utf-8")
    renderer_code = renderer_js[renderer_js.index("export function createDoodleRenderer("):]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page(viewport={"width": 1400, "height": 800})
        page.set_content(f"""
            <style>
                {chat_css}
                #image-container {{ position: relative; width: 1000px; height: 600px; }}
                #doodle-canvas {{ width: 100%; height: 100%; }}
                #chat-sidebar {{ position: relative; width: 250px; height: 600px; display: flex; flex-direction: column; }}
            </style>
            <div style="display:flex;">
                <div id="image-container">
                    <canvas id="doodle-canvas"></canvas>
                    <div id="canvas-stamp-layer"></div>
                </div>
                <div id="chat-sidebar">
                    <div id="panel-body" style="display: flex; flex-direction: column; flex: 1;">
                        <div id="chat-messages" style="flex:1;">Chat messages</div>
                        <form id="chat-form">
                            <textarea id="chat-input"></textarea>
                            <div class="chat-composer-actions">
                                <button type="button" id="chat-open-stamps-btn">🏷️</button>
                            </div>
                        </form>
                    </div>
                    <div id="stamp-manager-pane" style="display:none; flex-direction: column; flex: 1;">
                        <div class="stamp-manager-header">
                            <span id="stamp-manager-count">0/10</span>
                            <button type="button" id="close-stamp-manager-btn">✕ Back</button>
                        </div>
                        <div id="stamp-manager-permission-banner"></div>
                        <div id="stamp-manager-body" class="stamp-manager-body">
                            <div id="stamp-manager-grid" class="stamp-grid"></div>
                            <div id="stamp-manager-empty" style="display:none;">Empty</div>
                        </div>
                        <div class="stamp-manager-footer">
                            <button type="button" id="stamp-manager-back-chat-btn">Back</button>
                        </div>
                    </div>
                </div>
            </div>
        """)

        page.add_script_tag(content=renderer_code.replace("export function", "function"))
        page.add_script_tag(content="""
            const canvas = document.getElementById('doodle-canvas');
            const imgContainer = document.getElementById('image-container');
            const chatOpenStampsBtn = document.getElementById('chat-open-stamps-btn');
            const stampManagerPane = document.getElementById('stamp-manager-pane');
            const closeStampManagerBtn = document.getElementById('close-stamp-manager-btn');
            const stampManagerBackChatBtn = document.getElementById('stamp-manager-back-chat-btn');
            const panelBody = document.getElementById('panel-body');
            const stampManagerGrid = document.getElementById('stamp-manager-grid');
            const stampManagerCount = document.getElementById('stamp-manager-count');

            let isPagedBack = false;
            let contributorPermission = true;
            const hasContributorsPermission = () => contributorPermission;
            let currentUser = { id: 5, username: "Alice" };
            let myUserId = "Alice";
            let doodleActions = [];
            const sent = [];
            let cachedUserStamps = null;

            function sendOrQueueDoodleMessage(action) {
                if (!action.client_message_id) action.client_message_id = 'msg-' + sent.length;
                sent.push(action);
            }

            function getStampUsageStorageKey() {
                const uid = (currentUser && currentUser.id) ? currentUser.id : null;
                return uid ? `narratron_stamp_usage_${uid}` : 'narratron_stamp_usage';
            }

            function getStampUsageMap() {
                try {
                    const specificKey = getStampUsageStorageKey();
                    const rawSpecific = localStorage.getItem(specificKey);
                    if (rawSpecific) {
                        const parsed = JSON.parse(rawSpecific);
                        if (parsed && typeof parsed === 'object') return parsed;
                    }
                    const rawGlobal = localStorage.getItem('narratron_stamp_usage');
                    if (rawGlobal) {
                        const parsed = JSON.parse(rawGlobal);
                        if (parsed && typeof parsed === 'object') return parsed;
                    }
                } catch (_) {}
                return {};
            }

            function getStampLastUsedTimestamp(stampId) {
                const idStr = String(stampId);
                const map = getStampUsageMap();
                if (map && map[idStr]) {
                    const val = Number(map[idStr]);
                    if (!Number.isNaN(val)) return val;
                }

                if (Array.isArray(doodleActions)) {
                    for (let i = doodleActions.length - 1; i >= 0; i--) {
                        const action = doodleActions[i];
                        if (action && action.type === 'stamp' && String(action.stamp_id) === idStr) {
                            return 1000 + i;
                        }
                    }
                }
                return 0;
            }

            function recordStampUsage(stampId) {
                if (stampId === null || stampId === undefined) return;
                const idStr = String(stampId);
                const now = Date.now();
                const map = getStampUsageMap();
                map[idStr] = now;
                try {
                    const json = JSON.stringify(map);
                    localStorage.setItem(getStampUsageStorageKey(), json);
                    localStorage.setItem('narratron_stamp_usage', json);
                } catch (_) {}
                if (cachedUserStamps && cachedUserStamps.length > 0) {
                    renderStampManagerGrid(cachedUserStamps);
                }
            }

            function renderStampManagerGrid(stamps) {
                if (!stampManagerGrid) return;
                stampManagerGrid.innerHTML = '';
                if (stampManagerCount) stampManagerCount.textContent = `${stamps.length}/10`;
                if (stamps.length === 0) return;

                const sortedStamps = [...stamps].sort((a, b) => {
                    const timeA = getStampLastUsedTimestamp(a.id);
                    const timeB = getStampLastUsedTimestamp(b.id);
                    if (timeA !== timeB) {
                        return timeB - timeA;
                    }
                    const idA = Number(a.id) || 0;
                    const idB = Number(b.id) || 0;
                    return idA - idB;
                });

                const canDrag = hasContributorsPermission();
                sortedStamps.forEach(stamp => {
                    const card = document.createElement('div');
                    card.className = `stamp-card ${canDrag ? 'can-drag' : 'locked'}`;
                    card.draggable = canDrag;
                    card.dataset.stampId = String(stamp.id);
                    card.dataset.stampName = stamp.name || 'Stamp';
                    card.dataset.stampUrl = stamp.url;

                    const nameSpan = document.createElement('span');
                    nameSpan.className = 'stamp-card-name';
                    nameSpan.textContent = stamp.name;
                    card.appendChild(nameSpan);

                    stampManagerGrid.appendChild(card);
                });
            }

            function bringStampToFront(stampId) {
                const index = doodleActions.findIndex(
                    existing => existing.type === 'stamp' && existing.id === stampId
                );
                if (index >= 0) {
                    const [selected] = doodleActions.splice(index, 1);
                    doodleActions.push(selected);
                }
                const stampLayer = document.getElementById('canvas-stamp-layer');
                if (stampLayer) {
                    const el = Array.from(stampLayer.children).find(node => node.dataset.annotationId === stampId);
                    if (el && stampLayer.lastElementChild !== el) {
                        stampLayer.appendChild(el);
                    }
                }
            }

            function selectStampAnnotation(action) {
                bringStampToFront(action.id);
                if (action && action.stamp_id) {
                    recordStampUsage(action.stamp_id);
                }
                sendOrQueueDoodleMessage({ type: 'select_stamp', id: action.id });
            }

            function applyStampAnnotation(action) {
                const index = doodleActions.findIndex(
                    existing => existing.type === 'stamp' && (
                        (existing.id && existing.id === action.id) ||
                        (String(existing.stamp_id) === String(action.stamp_id) && String(existing.user_id) === String(action.user_id))
                    )
                );
                if (index >= 0) doodleActions.splice(index, 1);
                doodleActions.push(action);
                renderer.redraw(doodleActions);
            }

            function moveStampAnnotation(action, previous, commit) {
                if (commit) {
                    if (action && action.stamp_id) {
                        recordStampUsage(action.stamp_id);
                    }
                    action = { ...action };
                    delete action.client_message_id;
                    sendOrQueueDoodleMessage(action);
                }
                applyStampAnnotation(action);
            }

            function removeStampAnnotation(action) {
                const index = doodleActions.findIndex(
                    existing => existing.type === 'stamp' && existing.id === action.id
                );
                if (index >= 0) {
                    doodleActions.splice(index, 1);
                    sendOrQueueDoodleMessage({ type: 'remove_stamp', id: action.id });
                    renderer.redraw(doodleActions);
                }
            }

            function placeStampOnCanvas(stampData, normX, normY) {
                if (!hasContributorsPermission() || isPagedBack) return;
                const myId = (currentUser && currentUser.id) ? currentUser.id : myUserId;
                const action = {
                    type: 'stamp',
                    id: 'stamp-' + Math.random().toString(36).slice(2, 9),
                    stamp_id: stampData.stamp_id,
                    user_id: myId,
                    url: stampData.url,
                    name: stampData.name || 'Stamp',
                    x: normX,
                    y: normY,
                    size: 80,
                };
                const existingIndex = doodleActions.findIndex(
                    item => item.type === 'stamp' &&
                            String(item.stamp_id) === String(action.stamp_id) &&
                            String(item.user_id) === String(action.user_id)
                );
                if (existingIndex >= 0) {
                    action.id = doodleActions[existingIndex].id || action.id;
                    doodleActions.splice(existingIndex, 1);
                }
                doodleActions.push(action);
                recordStampUsage(stampData.stamp_id);
                sendOrQueueDoodleMessage(action);
                renderer.redraw(doodleActions);
            }

            const renderer = createDoodleRenderer({
                canvas,
                stampLayer: document.getElementById('canvas-stamp-layer'),
                canMoveStamp: () => hasContributorsPermission() && !isPagedBack,
                onMoveStamp: (action, previous, commit) => moveStampAnnotation(action, previous, commit),
                onRemoveStamp: action => removeStampAnnotation(action),
                onSelectStamp: action => selectStampAnnotation(action),
            });

            function setStampManagerOpen(open) {
                panelBody.style.display = open ? 'none' : 'flex';
                stampManagerPane.style.display = open ? 'flex' : 'none';
                chatOpenStampsBtn.classList.toggle('active', open);
            }

            chatOpenStampsBtn.addEventListener('click', () => {
                setStampManagerOpen(stampManagerPane.style.display === 'none');
            });
            closeStampManagerBtn.addEventListener('click', () => setStampManagerOpen(false));
            stampManagerBackChatBtn.addEventListener('click', () => setStampManagerOpen(false));

            renderer.resize(doodleActions);
        """)

        yield page
        browser.close()


def test_stamp_manager_toggles_and_occupies_chat_pane(stamp_page: Page) -> None:
    # Initially panelBody visible, stampManagerPane hidden
    assert stamp_page.locator("#panel-body").is_visible()
    assert not stamp_page.locator("#stamp-manager-pane").is_visible()

    # Click open stamp manager button
    stamp_page.locator("#chat-open-stamps-btn").click()
    assert stamp_page.locator("#stamp-manager-pane").is_visible()
    assert not stamp_page.locator("#panel-body").is_visible()

    # Click back to chat button
    stamp_page.locator("#close-stamp-manager-btn").click()
    assert stamp_page.locator("#panel-body").is_visible()
    assert not stamp_page.locator("#stamp-manager-pane").is_visible()


def test_stamp_drag_and_drop_enforces_one_of_a_kind_per_user(stamp_page: Page) -> None:
    # Place Stamp 10 at (0.2, 0.3)
    stamp_page.evaluate('placeStampOnCanvas({stamp_id: 10, name: "Dragon", url: "/api/stamps/10"}, 0.2, 0.3)')
    assert stamp_page.evaluate('doodleActions.length') == 1
    assert stamp_page.evaluate('doodleActions[0].x') == pytest.approx(0.2)
    assert stamp_page.evaluate('doodleActions[0].y') == pytest.approx(0.3)
    assert stamp_page.locator(".canvas-stamp-annotation").count() == 1

    # Place Stamp 11 (different kind) at (0.5, 0.5)
    stamp_page.evaluate('placeStampOnCanvas({stamp_id: 11, name: "Heart", url: "/api/stamps/11"}, 0.5, 0.5)')
    assert stamp_page.evaluate('doodleActions.length') == 2
    assert stamp_page.locator(".canvas-stamp-annotation").count() == 2

    # Pulling another Stamp 10 replaces previous Stamp 10 at new position (0.8, 0.9)
    stamp_page.evaluate('placeStampOnCanvas({stamp_id: 10, name: "Dragon", url: "/api/stamps/10"}, 0.8, 0.9)')
    # Total count must still be 2!
    assert stamp_page.evaluate('doodleActions.length') == 2
    assert stamp_page.locator(".canvas-stamp-annotation").count() == 2
    # Verify Dragon's position is now 0.8, 0.9
    dragon_x = stamp_page.evaluate('doodleActions.find(s => s.stamp_id === 10).x')
    dragon_y = stamp_page.evaluate('doodleActions.find(s => s.stamp_id === 10).y')
    assert dragon_x == pytest.approx(0.8)
    assert dragon_y == pytest.approx(0.9)


def test_stamp_selection_and_keyboard_removal(stamp_page: Page) -> None:
    stamp_page.evaluate('placeStampOnCanvas({stamp_id: 10, name: "Dragon", url: "/api/stamps/10"}, 0.4, 0.4)')
    stamp = stamp_page.locator(".canvas-stamp-annotation").first
    assert stamp.is_visible()
    assert "movable" not in (stamp.get_attribute("class") or "")

    # Click to select
    stamp.click()
    assert "movable" in (stamp.get_attribute("class") or "")

    # Press Delete key to remove
    stamp_page.keyboard.press("Delete")
    assert stamp_page.evaluate('doodleActions.length') == 0
    assert stamp_page.locator(".canvas-stamp-annotation").count() == 0


def test_stamp_click_and_hold_immediate_drag(stamp_page: Page) -> None:
    # 1. Place a stamp at (0.3, 0.3) without selecting it first
    stamp_page.evaluate('placeStampOnCanvas({stamp_id: 10, name: "Dragon", url: "/api/stamps/10"}, 0.3, 0.3)')
    stamp = stamp_page.locator(".canvas-stamp-annotation").first
    assert stamp.is_visible()
    assert "movable" not in (stamp.get_attribute("class") or "")

    box = stamp.bounding_box()
    assert box is not None
    start_x = box["x"] + box["width"] / 2
    start_y = box["y"] + box["height"] / 2

    # 2. Click and hold immediately and drag without a prior click
    stamp_page.mouse.move(start_x, start_y)
    stamp_page.mouse.down()
    stamp_page.mouse.move(start_x + 100, start_y + 100)
    stamp_page.mouse.up()

    # The stamp should have moved immediately
    new_x = stamp_page.evaluate('doodleActions[0].x')
    new_y = stamp_page.evaluate('doodleActions[0].y')
    assert new_x > 0.3
    assert new_y > 0.3


def test_stamp_placement_rejected_without_contributor_permission(stamp_page: Page) -> None:
    # Disable contributor permission
    stamp_page.evaluate('contributorPermission = false')
    stamp_page.evaluate('placeStampOnCanvas({stamp_id: 10, name: "Dragon", url: "/api/stamps/10"}, 0.3, 0.3)')

    # Nothing placed
    assert stamp_page.evaluate('doodleActions.length') == 0
    assert stamp_page.locator(".canvas-stamp-annotation").count() == 0


def test_stamp_resizing_via_handle_and_wheel(stamp_page: Page) -> None:
    # Place stamp at center (0.5, 0.5) with default size 80
    stamp_page.evaluate('placeStampOnCanvas({stamp_id: 10, name: "Dragon", url: "/api/stamps/10"}, 0.5, 0.5)')
    stamp = stamp_page.locator(".canvas-stamp-annotation").first
    assert stamp.is_visible()
    assert stamp.evaluate("el => el.style.width") == "80px"
    assert stamp.evaluate("el => el.style.height") == "80px"

    # Select the stamp so handle is displayed and interactions are enabled
    stamp.click()
    assert "movable" in (stamp.get_attribute("class") or "")
    resize_handle = stamp.locator(".stamp-resize-handle")
    assert resize_handle.is_visible()

    # Wheel up should increase size (+8px -> 88px)
    stamp.dispatch_event("wheel", {"deltaY": -100})
    assert stamp_page.evaluate("doodleActions[0].size") == 88
    assert stamp.evaluate("el => el.style.width") == "88px"
    assert stamp.evaluate("el => el.style.height") == "88px"

    # Wheel down should decrease size (-8px -> 80px)
    stamp.dispatch_event("wheel", {"deltaY": 100})
    assert stamp_page.evaluate("doodleActions[0].size") == 80
    assert stamp.evaluate("el => el.style.width") == "80px"

    # Drag resize handle to expand stamp size
    box = resize_handle.bounding_box()
    assert box is not None
    stamp_page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    stamp_page.mouse.down()
    stamp_page.mouse.move(box["x"] + box["width"] / 2 + 50, box["y"] + box["height"] / 2 + 50)
    stamp_page.mouse.up()

    # Size should now be greater than 80px
    new_size = stamp_page.evaluate("doodleActions[0].size")
    assert new_size > 80
    assert stamp.evaluate("el => el.style.width") == f"{new_size}px"
    assert stamp.evaluate("el => el.style.height") == f"{new_size}px"


def test_stamp_hover_cursor_styles(stamp_page: Page) -> None:
    # 1. Canvas stamp cursor: grab on hover, grabbing on active
    stamp_page.evaluate('placeStampOnCanvas({stamp_id: 10, name: "Dragon", url: "/api/stamps/10"}, 0.5, 0.5)')
    stamp = stamp_page.locator(".canvas-stamp-annotation").first
    assert stamp.is_visible()
    cursor = stamp.evaluate("el => window.getComputedStyle(el).cursor")
    assert cursor == "grab"

    # 2. Stamp manager card cursor: grab when draggable, not-allowed when locked
    stamp_page.evaluate("""
        const grid = document.getElementById('stamp-manager-grid');
        grid.innerHTML = `
            <div class="stamp-card can-drag" id="test-card-drag">Stamp 1</div>
            <div class="stamp-card locked" id="test-card-locked">Stamp 2</div>
        `;
    """)
    drag_card = stamp_page.locator("#test-card-drag")
    assert drag_card.evaluate("el => window.getComputedStyle(el).cursor") == "grab"

    locked_card = stamp_page.locator("#test-card-locked")
    assert locked_card.evaluate("el => window.getComputedStyle(el).cursor") == "not-allowed"


def test_stamps_dynamically_resize_when_canvas_resizes(stamp_page: Page) -> None:
    # 1. Place stamp with default size 80 on a 1000px wide canvas -> 80px
    stamp_page.evaluate('placeStampOnCanvas({stamp_id: 10, name: "Dragon", url: "/api/stamps/10"}, 0.5, 0.5)')
    stamp = stamp_page.locator(".canvas-stamp-annotation").first
    assert stamp.is_visible()
    assert stamp.evaluate("el => el.style.width") == "80px"
    assert stamp.evaluate("el => el.style.height") == "80px"

    # 2. Resize canvas container to 500px -> stamp dynamically resizes to 40px
    stamp_page.evaluate("""
        const container = document.getElementById('image-container');
        container.style.width = '500px';
        renderer.resize(doodleActions);
    """)
    assert stamp.evaluate("el => el.style.width") == "40px"
    assert stamp.evaluate("el => el.style.height") == "40px"

    # 3. Resize canvas container to 800px -> stamp dynamically resizes to 64px
    stamp_page.evaluate("""
        const container = document.getElementById('image-container');
        container.style.width = '800px';
        renderer.resize(doodleActions);
    """)
    assert stamp.evaluate("el => el.style.width") == "64px"
    assert stamp.evaluate("el => el.style.height") == "64px"


def test_stamp_layering_order_creation_and_selection(stamp_page: Page) -> None:
    # 1. Place Stamp 10 (Dragon) at (0.3, 0.3)
    stamp_page.evaluate('placeStampOnCanvas({stamp_id: 10, name: "Dragon", url: "/api/stamps/10"}, 0.3, 0.3)')

    # 2. Place Stamp 11 (Heart) at (0.35, 0.35)
    stamp_page.evaluate('placeStampOnCanvas({stamp_id: 11, name: "Heart", url: "/api/stamps/11"}, 0.35, 0.35)')

    # Heart was created most recently, so Heart must be at the front (last child of #canvas-stamp-layer)
    assert stamp_page.evaluate('document.getElementById("canvas-stamp-layer").lastElementChild.dataset.annotationId') == stamp_page.evaluate('doodleActions[1].id')
    assert stamp_page.evaluate('doodleActions[0].stamp_id') == 10
    assert stamp_page.evaluate('doodleActions[1].stamp_id') == 11

    # 3. Select Dragon (click Stamp 10) -> Dragon must move to front!
    dragon_id = stamp_page.evaluate('doodleActions[0].id')
    dragon_locator = stamp_page.locator(f'.canvas-stamp-annotation[data-annotation-id="{dragon_id}"]')
    dragon_locator.click()

    # Now Dragon should be the last child (at the front)!
    assert stamp_page.evaluate('document.getElementById("canvas-stamp-layer").lastElementChild.dataset.annotationId') == dragon_id
    assert stamp_page.evaluate('doodleActions[1].stamp_id') == 10
    assert stamp_page.evaluate('doodleActions[0].stamp_id') == 11

    # Verify select_stamp message was sent
    select_messages = stamp_page.evaluate('sent.filter(m => m.type === "select_stamp")')
    assert len(select_messages) >= 1
    assert select_messages[-1]["id"] == dragon_id

    # 4. Select Heart (click Stamp 11) -> Heart must move to front!
    heart_id = stamp_page.evaluate('doodleActions[0].id')
    heart_locator = stamp_page.locator(f'.canvas-stamp-annotation[data-annotation-id="{heart_id}"]')
    heart_locator.click()

    assert stamp_page.evaluate('document.getElementById("canvas-stamp-layer").lastElementChild.dataset.annotationId') == heart_id
    assert stamp_page.evaluate('doodleActions[1].stamp_id') == 11
    assert stamp_page.evaluate('doodleActions[0].stamp_id') == 10

    # 5. Click background to deselect -> Heart must still remain at the front!
    stamp_page.locator('#doodle-canvas').click(position={'x': 10, 'y': 10})
    assert stamp_page.evaluate('document.getElementById("canvas-stamp-layer").lastElementChild.dataset.annotationId') == heart_id

    # 6. Re-placing Dragon replaces it and moves it to the front!
    stamp_page.evaluate('placeStampOnCanvas({stamp_id: 10, name: "Dragon", url: "/api/stamps/10"}, 0.7, 0.7)')
    assert stamp_page.evaluate('document.getElementById("canvas-stamp-layer").lastElementChild.dataset.annotationId') == dragon_id
    assert stamp_page.evaluate('doodleActions[1].stamp_id') == 10
    assert stamp_page.evaluate('doodleActions[0].stamp_id') == 11


def test_stamp_manager_ordered_from_most_recently_used_to_least(stamp_page: Page) -> None:
    # Initialize cachedUserStamps with 3 stamps: ID 1 (Star), ID 2 (Moon), ID 3 (Sun)
    stamp_page.evaluate("""
        cachedUserStamps = [
            { id: 1, name: "Star", url: "/api/stamps/1" },
            { id: 2, name: "Moon", url: "/api/stamps/2" },
            { id: 3, name: "Sun", url: "/api/stamps/3" },
        ];
        renderStampManagerGrid(cachedUserStamps);
    """)

    # Initial order: 1, 2, 3
    card_ids = stamp_page.evaluate(
        'Array.from(document.querySelectorAll("#stamp-manager-grid .stamp-card")).map(c => c.dataset.stampId)'
    )
    assert card_ids == ["1", "2", "3"]

    # Place Stamp 3 (Sun) on canvas -> Sun is now most recently used!
    stamp_page.evaluate('placeStampOnCanvas({stamp_id: 3, name: "Sun", url: "/api/stamps/3"}, 0.2, 0.2)')
    card_ids_after_3 = stamp_page.evaluate(
        'Array.from(document.querySelectorAll("#stamp-manager-grid .stamp-card")).map(c => c.dataset.stampId)'
    )
    # 3 must now be first, followed by 1 and 2
    assert card_ids_after_3 == ["3", "1", "2"]

    # Place Stamp 1 (Star) on canvas -> Star is now most recently used!
    stamp_page.evaluate('placeStampOnCanvas({stamp_id: 1, name: "Star", url: "/api/stamps/1"}, 0.5, 0.5)')
    card_ids_after_1 = stamp_page.evaluate(
        'Array.from(document.querySelectorAll("#stamp-manager-grid .stamp-card")).map(c => c.dataset.stampId)'
    )
    # Order: 1 (most recent), 3 (previous), 2 (least recent / unused)
    assert card_ids_after_1 == ["1", "3", "2"]

    # Re-place Stamp 2 (Moon) on canvas -> Moon is now most recently used!
    stamp_page.evaluate('placeStampOnCanvas({stamp_id: 2, name: "Moon", url: "/api/stamps/2"}, 0.7, 0.7)')
    card_ids_after_2 = stamp_page.evaluate(
        'Array.from(document.querySelectorAll("#stamp-manager-grid .stamp-card")).map(c => c.dataset.stampId)'
    )
    # Order: 2 (most recent), 1, 3
    assert card_ids_after_2 == ["2", "1", "3"]

    # Selecting Stamp 3 on the canvas also makes Stamp 3 most recently used!
    sun_annotation_id = stamp_page.evaluate('doodleActions.find(a => a.stamp_id === 3).id')
    sun_card = stamp_page.locator(f'.canvas-stamp-annotation[data-annotation-id="{sun_annotation_id}"]')
    sun_card.click()

    card_ids_after_select_3 = stamp_page.evaluate(
        'Array.from(document.querySelectorAll("#stamp-manager-grid .stamp-card")).map(c => c.dataset.stampId)'
    )
    # Order: 3 (selected most recently), 2, 1
    assert card_ids_after_select_3 == ["3", "2", "1"]

    # Verify order is preserved when re-rendering (e.g. reopening stamp manager or reloading)
    stamp_page.evaluate('renderStampManagerGrid(cachedUserStamps)')
    card_ids_rerender = stamp_page.evaluate(
        'Array.from(document.querySelectorAll("#stamp-manager-grid .stamp-card")).map(c => c.dataset.stampId)'
    )
    assert card_ids_rerender == ["3", "2", "1"]




