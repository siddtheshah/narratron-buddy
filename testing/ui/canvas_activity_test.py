"""Keep tool indicator animation timelines stable across canvas refreshes."""

from collections.abc import Iterator
from pathlib import Path
import sys

from playwright.sync_api import Page, Route, expect, sync_playwright
import pytest

from testing.ui.canvas_source import read_ui_source


@pytest.fixture
def activity_page() -> Iterator[Page]:
    template = read_ui_source(Path("templates/canvas.html"))
    css = template.split("<style>", 1)[1].split("</style>", 1)[0]
    start = template.index('            <div id="agent-activity-tray"')
    end = template.index('id="agent-disconnected-indicator"', start)
    end = template.index("</div>", end) + len("</div>")
    markup = template[start:end] + "</div>"
    start = template.index("        const pcmBroadcastIndicator =")
    end = template.index("        function setPcmBroadcasting", start)
    script = template[start:end]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge" if sys.platform == "win32" else None)
        page = browser.new_page()

        def serve_shell(route: Route) -> None:
            route.fulfill(body=f"<style>{css}</style>{markup}<script>{script}</script>")

        page.route("http://activity.test/", serve_shell)
        page.goto("http://activity.test/")
        yield page
        browser.close()


def test_long_running_tool_indicators_keep_their_animation_timelines(activity_page: Page) -> None:
    page = activity_page
    result = page.evaluate("""async () => {
        const activity = {image_generating: true, animation_generating: true,
            user_action_processing: true, music_generating: true,
            reference_generating: true, dice_rolling: true,
            dice_result: {tier: 'high', notation: '1d20', total: 20}};
        updateAgentActivity(activity);
        const icons = [...document.querySelectorAll('.agent-activity-icon.active')];
        const animations = icons.map(icon => icon.getAnimations());
        await Promise.all(animations.flat().map(animation => animation.ready));
        // Seek past many animation cycles without real-time waits.
        animations.flat().forEach(animation => { animation.currentTime = 120250; });
        const mutations = [];
        const observer = new MutationObserver(records => mutations.push(...records));
        icons.forEach(icon => observer.observe(icon, {attributes: true,
            attributeFilter: ['class', 'aria-hidden']}));
        for (let refresh = 0; refresh < 10; refresh++) {
            updateAgentActivity(activity);
            await new Promise(requestAnimationFrame);
        }
        observer.disconnect();
        return {
            count: icons.length,
            mutations: mutations.length,
            stable: icons.every((icon, index) => {
                const current = icon.getAnimations();
                return current.length > 0 && current.length === animations[index].length
                    && current.every((animation, slot) => animation === animations[index][slot]
                    && animation.playState === 'running'
                    && animation.currentTime >= 120250
                    && animation.effect.getTiming().iterations === Infinity);
            }),
        };
    }""")
    assert result == {"count": 5, "mutations": 0, "stable": True}


def test_partial_refreshes_keep_book_and_music_active_until_explicit_completion(activity_page: Page) -> None:
    page = activity_page
    page.evaluate("""() => {
        updateAgentActivity({user_action_processing: true, music_generating: true});
        updateAgentActivity();
        updateAgentActivity(null);
        updateAgentActivity({reference_generating: true});
    }""")
    book = page.locator("#agent-writing-indicator")
    music = page.locator("#agent-music-indicator")
    expect(book).to_be_visible()
    expect(music).to_be_visible()
    expect(book).to_have_attribute("aria-hidden", "false")
    page.evaluate("updateAgentActivity({user_action_processing: false})")
    expect(book).to_be_hidden()
    expect(music).to_be_visible()
    page.evaluate("updateAgentActivity({music_generating: false})")
    expect(music).to_be_hidden()
    expect(music).to_have_attribute("aria-hidden", "true")


def test_drawing_stays_active_until_both_visual_tools_complete(activity_page: Page) -> None:
    page = activity_page
    drawing = page.locator("#agent-drawing-indicator")
    page.evaluate("updateAgentActivity({image_generating: true, animation_generating: true})")
    page.evaluate("updateAgentActivity({image_generating: false})")
    expect(drawing).to_be_visible()
    expect(drawing).to_have_attribute("title", "Agent is generating an animation")
    page.evaluate("updateAgentActivity({animation_generating: false})")
    expect(drawing).to_be_hidden()


def test_unconfirmed_indicators_expire_and_can_recover(activity_page: Page) -> None:
    page = activity_page
    page.clock.install()
    page.evaluate("updateAgentActivity({user_action_processing: true, music_generating: true})")
    page.clock.run_for(29000)
    book = page.locator("#agent-writing-indicator")
    music = page.locator("#agent-music-indicator")
    expect(book).to_be_visible()
    expect(music).to_be_visible()
    # Confirming music or the live connection must not renew the book's lease.
    page.evaluate("updateAgentActivity({music_generating: true, live_ready: true})")
    page.clock.run_for(1000)
    expect(book).to_be_hidden()
    expect(music).to_be_visible()
    page.clock.run_for(29000)
    expect(music).to_be_hidden()
    page.evaluate("updateAgentActivity({user_action_processing: true})")
    expect(book).to_be_visible()


def test_confirmed_activity_loops_until_hard_timeout_without_reappearing(activity_page: Page) -> None:
    page = activity_page
    page.clock.install()
    page.evaluate("""() => {
        window.confirmTools = () => updateAgentActivity({
            user_action_processing: true, music_generating: true});
        confirmTools();
        window.confirmationTimer = setInterval(confirmTools, 10000);
    }""")
    book = page.locator("#agent-writing-indicator")
    music = page.locator("#agent-music-indicator")
    page.clock.run_for(599000)
    expect(book).to_be_visible()
    expect(music).to_be_visible()
    page.clock.run_for(1000)
    expect(book).to_be_hidden()
    expect(music).to_be_hidden()
    page.clock.run_for(60000)
    expect(book).to_be_hidden()
    expect(music).to_be_hidden()
    page.evaluate("""() => {
        clearInterval(window.confirmationTimer);
        updateAgentActivity({user_action_processing: false, music_generating: false});
        confirmTools();
    }""")
    expect(book).to_be_visible()
    expect(music).to_be_visible()
