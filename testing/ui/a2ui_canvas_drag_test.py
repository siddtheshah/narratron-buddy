"""Exercise surface dragging with the production renderer and canvas styles."""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright


@pytest.fixture
def surface_page() -> Iterator[Page]:
    template = Path("templates/canvas.html").read_text(encoding="utf-8")
    styles = template[template.index("        #a2ui-canvas-layer {") :
                      template.index("        @media (max-width: 700px) {", template.index("        @keyframes a2ui-arrive {"))]
    renderer = Path("static/js/a2ui-canvas-renderer.js").read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="msedge" if sys.platform == "win32" else None,
        )
        page = browser.new_page(viewport={"width": 1000, "height": 800})
        page.set_content(
            f"<style>{styles}</style>"
            '<div id="a2ui-canvas-layer"></div>'
        )
        page.add_script_tag(content=renderer.replace("export function", "function"))
        page.evaluate("""() => {
            window.requests = [];
            window.fetch = async (url, options) => {
                window.requests.push({url, ...options});
                return {ok: true};
            };
            window.editable = true;
            window.renderer = createA2UICanvasRenderer({
                container: document.querySelector('#a2ui-canvas-layer'),
                actionUrl: () => '/action',
                surfaceUrl: id => '/surfaces/' + id,
                canEdit: () => window.editable,
            });
            window.surfaces = [{
                surface_id: 'choice',
                placement: {left_pct: 50, top_pct: 50, width_pct: 28},
                messages: [{createSurface: {
                    surfaceId: 'choice',
                    components: [
                        {id: 'root', component: 'Card', child: 'button'},
                        {id: 'button', component: 'Button', child: 'label',
                         action: {event: {name: 'choose'}}},
                        {id: 'label', component: 'Text', text: 'Choose'},
                    ],
                }}],
            }];
            window.renderer.render(window.surfaces);
        }""")
        page.evaluate("async () => { await Promise.all(document.getAnimations().map(a => a.finished)); }")
        yield page
        browser.close()


@pytest.mark.parametrize("edge", ["top", "bottom", "left", "right", "control"])
def test_drag_preserves_grab_offset_and_saves_position(surface_page: Page, edge: str) -> None:
    surface = surface_page.locator(".a2ui-surface")
    bounds = surface.bounding_box()
    assert bounds is not None
    x = bounds["x"] + bounds["width"] / 2
    y = bounds["y"] + bounds["height"] / 2
    if edge == "top":
        y = bounds["y"] + 10
    elif edge == "bottom":
        y = bounds["y"] + bounds["height"] - 10
    elif edge == "left":
        x = bounds["x"] + 10
    elif edge == "right":
        x = bounds["x"] + bounds["width"] - 10
    else:
        control = surface_page.locator(".a2ui-move").bounding_box()
        assert control is not None
        x = control["x"] + control["width"] / 2
        y = control["y"] + control["height"] / 2
    surface_page.mouse.move(x, y)
    surface_page.mouse.down()
    surface_page.mouse.move(x + 100, y + 80)
    assert surface.evaluate("el => el.style.left") == "60%"
    assert surface.evaluate("el => el.style.top") == "60%"
    surface_page.mouse.up()
    assert surface_page.evaluate("window.requests") == [{
        "url": "/surfaces/choice", "method": "PATCH",
        "headers": {"Content-Type": "application/json"},
        "body": '{"left_pct":60,"top_pct":60}',
    }]
    assert "dragging" not in surface.get_attribute("class").split()


def test_content_button_still_sends_action(surface_page: Page) -> None:
    surface_page.locator(".a2ui-button").click()
    assert surface_page.evaluate("window.requests.map(r => r.url)") == ["/action"]


def test_cancelled_drag_restores_position(surface_page: Page) -> None:
    surface_page.mouse.move(370, 400)
    surface_page.mouse.down()
    surface_page.mouse.move(470, 480)
    surface_page.locator(".a2ui-surface").evaluate("""el => {
        el.dispatchEvent(new PointerEvent('pointercancel', {pointerId: 1}));
    }""")
    surface_page.mouse.up()
    assert surface_page.locator(".a2ui-surface").evaluate("el => el.style.left") == "50%"
    assert surface_page.evaluate("window.requests") == []


def test_read_only_surface_cannot_be_dragged(surface_page: Page) -> None:
    surface_page.evaluate("""() => {
        window.editable = false;
        window.renderer.render([]);
        window.renderer.render(window.surfaces);
    }""")
    assert surface_page.locator(".a2ui-surface-editable").count() == 0
    surface_page.mouse.move(370, 400)
    surface_page.mouse.down()
    surface_page.mouse.move(470, 480)
    surface_page.mouse.up()
    assert surface_page.locator(".a2ui-surface").evaluate("el => el.style.left") == "50%"
    assert surface_page.evaluate("window.requests") == []


def test_position_update_preserves_surfaces_and_does_not_replay_arrival(surface_page: Page) -> None:
    surface_page.evaluate("""() => {
        const other = structuredClone(window.surfaces[0]);
        other.surface_id = 'other';
        other.messages[0].createSurface.surfaceId = 'other';
        other.placement.left_pct = 80;
        window.surfaces.push(other);
        window.renderer.render(window.surfaces);
        window.originalHosts = [...document.querySelectorAll('.a2ui-surface')];
        window.originalButtons = [...document.querySelectorAll('.a2ui-button')];
    }""")
    surface_page.evaluate("async () => { await Promise.all(document.getAnimations().map(a => a.finished)); }")
    surface_page.evaluate("""() => {
        window.arrivals = [];
        document.addEventListener('animationstart', event => window.arrivals.push(event.animationName));
    }""")
    move = surface_page.locator('[data-surface-id="choice"] .a2ui-move')
    bounds = move.bounding_box()
    assert bounds is not None
    x = bounds["x"] + bounds["width"] / 2
    y = bounds["y"] + bounds["height"] / 2
    surface_page.mouse.move(x, y)
    surface_page.mouse.down()
    surface_page.mouse.move(x + 100, y + 80)
    surface_page.mouse.up()
    surface_page.evaluate("""() => {
        window.surfaces[0].placement.left_pct = 60;
        window.surfaces[0].placement.top_pct = 60;
        window.renderer.render(window.surfaces);
    }""")
    assert surface_page.evaluate("""() => window.originalHosts.every((host, i) =>
        host === document.querySelectorAll('.a2ui-surface')[i])""")
    assert surface_page.evaluate("""() => window.originalButtons.every((button, i) =>
        button === document.querySelectorAll('.a2ui-button')[i])""")
    surface_page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    assert surface_page.evaluate("window.arrivals") == []
    assert surface_page.evaluate("window.requests.map(r => r.method)") == ["PATCH"]


def test_remote_position_update_is_used_for_next_drag(surface_page: Page) -> None:
    surface_page.evaluate("""() => {
        window.surfaces[0].placement.left_pct = 60;
        window.surfaces[0].placement.top_pct = 60;
        window.renderer.render(window.surfaces);
    }""")
    bounds = surface_page.locator('.a2ui-move').bounding_box()
    assert bounds is not None
    x = bounds["x"] + bounds["width"] / 2
    y = bounds["y"] + bounds["height"] / 2
    surface_page.mouse.move(x, y)
    surface_page.mouse.down()
    surface_page.mouse.move(x + 100, y + 80)
    surface_page.mouse.up()
    assert surface_page.evaluate("JSON.parse(window.requests[0].body)") == {
        "left_pct": 70, "top_pct": 70,
    }


def test_content_updates_and_surface_lifecycle_preserve_existing_host(surface_page: Page) -> None:
    surface_page.evaluate("""() => {
        window.originalHost = document.querySelector('.a2ui-surface');
        window.originalAnimation = window.originalHost.getAnimations()[0];
        window.surfaces[0].messages.push({updateComponents: {components: [
            {id: 'label', component: 'Text', text: 'Updated choice'},
        ]}});
        const added = structuredClone(window.surfaces[0]);
        added.surface_id = 'added';
        added.messages[0].createSurface.surfaceId = 'added';
        window.surfaces.push(added);
        window.renderer.render(window.surfaces);
    }""")
    assert surface_page.locator('[data-surface-id="choice"] .a2ui-button').inner_text() == "Updated choice"
    assert surface_page.evaluate("window.originalHost === document.querySelector('[data-surface-id=choice]')")
    assert surface_page.evaluate("window.originalHost.getAnimations()[0] === window.originalAnimation")
    assert surface_page.locator('[data-surface-id="added"]').evaluate(
        "el => el.getAnimations().some(a => a.animationName === 'a2ui-arrive')"
    )
    surface_page.evaluate("""() => {
        window.surfaces.splice(0, 1);
        window.renderer.render(window.surfaces);
    }""")
    assert surface_page.locator('[data-surface-id="choice"]').count() == 0
    assert surface_page.locator('[data-surface-id="added"]').count() == 1
